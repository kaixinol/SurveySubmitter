from __future__ import annotations

import copy
import dataclasses
from loguru import logger
from typing import Any, Callable, cast

from survey_submitter.constants import DEFAULT_FILL_TEXT
from survey_submitter.core.config.schema import QuestionInfo
from survey_submitter.core.questions.meta_helpers import (
    infer_question_entry_type,
    normalize_attached_selects,
    normalize_fillable_indices,
)
from survey_submitter.core.questions.schema import (
    ChoiceQuestionAnswerConfig,
    LocationQuestionAnswerConfig,
    MultiTextQuestionAnswerConfig,
    QuestionAnswerConfig,
    QuestionDetail,
    TextQuestionAnswerConfig,
    answer_config_type_for_question_type,
)
from survey_submitter.core.questions.schema import (
    _TEXT_RANDOM_ID_CARD,
    _TEXT_RANDOM_MOBILE,
    _TEXT_RANDOM_NAME,
    _TEXT_RANDOM_NONE,
)
from survey_submitter.core.questions.types import (
    QuestionType,
    TypeCode,
    CHOICE_TYPES,
    TEXT_TYPES,
    CHOICE_LIKE_TYPES,
)
from survey_submitter.providers.contracts import (
    ChoiceQuestionMeta,
    MatrixQuestionMeta,
    RatingQuestionMeta,
    SliderQuestionMeta,
    SurveyQuestionMeta,
    TextQuestionMeta,
)
from survey_submitter.providers.common import (
    SURVEY_PROVIDER_WJX,
    detect_survey_provider,
    normalize_survey_provider,
)

__all__ = ["build_default_survey_questions"]

DEFAULT_MULTIPLE_PROBABILITY = 50.0
DEFAULT_SLIDER_MAX = 100.0


def _build_mid_bias_weights(option_count: int) -> list[float]:
    count = max(1, int(option_count or 1))
    return [1.0] * count


def _normalize_question_num(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float, str)):
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None
    return None


def _normalize_title(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return "".join(text.split())


def _normalize_provider_key(
    raw_provider: object, raw_question_id: object
) -> tuple[str, str] | None:
    provider = normalize_survey_provider(raw_provider, default=SURVEY_PROVIDER_WJX)
    question_id = str(raw_question_id or "").strip()
    if not question_id:
        return None
    return provider, question_id


def _normalize_forced_option_index(raw: object, option_count: int) -> int | None:
    if not isinstance(raw, (int, float, str)):
        return None
    try:
        idx = int(raw)
    except (ValueError, TypeError):
        return None
    total = max(0, int(option_count or 0))
    if 0 <= idx < total:
        return idx
    return None


def _build_forced_single_weights(option_count: int, forced_index: int) -> list[float]:
    total = max(1, int(option_count or 1))
    return [1.0 if idx == forced_index else 0.0 for idx in range(total)]


def _infer_multi_text_blank_modes(q: SurveyQuestionMeta, blank_count: int) -> list[str]:
    labels = [
        str(item or "").strip()
        for item in list(
            q.text_input_labels if isinstance(q, TextQuestionMeta) and q.text_input_labels else []
        )
    ]
    title = str(q.title or "").strip()
    modes: list[str] = []
    for index in range(max(0, int(blank_count or 0))):
        text = labels[index] if index < len(labels) else ""
        if not text and blank_count <= 1:
            text = title
        normalized = "".join(str(text or "").split()).lower()
        if any(
            marker in normalized
            for marker in ("手机号", "手机号码", "手机", "电话", "联系电话", "联系方式")
        ):
            modes.append(_TEXT_RANDOM_MOBILE)
        elif any(marker in normalized for marker in ("身份证", "证件号", "证件号码")):
            modes.append(_TEXT_RANDOM_ID_CARD)
        elif any(marker in normalized for marker in ("姓名", "名字", "联系人")):
            modes.append(_TEXT_RANDOM_NAME)
        else:
            modes.append(_TEXT_RANDOM_NONE)
    return modes


def _filter_option_fill_texts_to_fillable(
    option_fill_texts: object,
    option_count: int,
    fillable_indices: list[int],
) -> list[str | None] | None:
    if not isinstance(option_fill_texts, list) or not fillable_indices:
        return None
    total = max(0, int(option_count or 0))
    fillable_set: set[int] = set()
    for raw_index in fillable_indices:
        try:
            option_index = int(raw_index)
        except (ValueError, TypeError):
            continue
        if 0 <= option_index < total:
            fillable_set.add(option_index)
    if not fillable_set:
        return None
    normalized: list[str | None] = []
    for option_index in range(total):
        raw_value = (
            option_fill_texts[option_index] if option_index < len(option_fill_texts) else None
        )
        text = str(raw_value or "").strip()
        normalized.append(text if option_index in fillable_set and text else None)
    return normalized if any(normalized) else None


# ---------------------------------------------------------------------------
# Intermediate data carriers
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _QuestionAttrs:
    """Attributes extracted from a single *SurveyQuestionMeta*."""

    num: object
    option_count: int
    rows: int
    text_inputs: int
    slider_min: object
    slider_max: object
    title_text: str
    forced_option_text: str
    forced_option_index: int | None
    attached_option_selects: list[object]
    provider_question_id: str
    provider_page_id: str
    q_type: QuestionType
    parsed_title_key: str


@dataclasses.dataclass
class _ResolvedConfig:
    """Fully-resolved configuration values ready for *QuestionInfo* construction."""

    probabilities: object
    distribution: str
    custom_weights: object
    option_count: int
    ai_enabled: bool
    text_random_mode: str
    text_random_int_range: list[object]
    multi_text_blank_modes: list[str]
    multi_text_blank_ai_flags: list[bool]
    multi_text_blank_int_ranges: list[list[int]]
    option_fill_texts: object
    fillable_indices: object
    attached_selects: list[object]
    location_parts: list[str]


# ---------------------------------------------------------------------------
# Helper functions extracted from build_default_survey_questions
# ---------------------------------------------------------------------------


def _build_existing_entry_maps(
    existing_entries: list[QuestionInfo] | None,
) -> tuple[
    dict[int, QuestionInfo], dict[str, QuestionInfo], dict[tuple[str, str], QuestionInfo]
]:
    """Build lookup dicts for existing entries keyed by number, title, and provider."""
    existing_by_num: dict[int, QuestionInfo] = {}
    existing_by_title: dict[str, QuestionInfo] = {}
    existing_by_provider: dict[tuple[str, str], QuestionInfo] = {}
    if existing_entries:
        for qi in existing_entries:
            q_num = _normalize_question_num(qi.num)
            if q_num is not None and q_num not in existing_by_num:
                existing_by_num[q_num] = qi
            title_key = _normalize_title(qi.title)
            if title_key and title_key not in existing_by_title:
                existing_by_title[title_key] = qi
            provider_key = _normalize_provider_key(
                qi.details.provider_question_id or "wjx",
                qi.details.provider_question_id,
            )
            if provider_key and provider_key not in existing_by_provider:
                existing_by_provider[provider_key] = qi
    return existing_by_num, existing_by_title, existing_by_provider


def _extract_question_attrs(
    q: SurveyQuestionMeta,
) -> _QuestionAttrs:
    """Pull all relevant attributes out of a *SurveyQuestionMeta*."""
    option_texts = q.option_texts if isinstance(q, ChoiceQuestionMeta) and q.option_texts else []
    option_count = len(option_texts)
    rows = q.rows if isinstance(q, MatrixQuestionMeta) else 1
    text_inputs = q.text_inputs if isinstance(q, TextQuestionMeta) else 0
    slider_min = q.slider_min if isinstance(q, SliderQuestionMeta) else None
    slider_max = q.slider_max if isinstance(q, SliderQuestionMeta) else None
    rating_max = q.rating_max if isinstance(q, RatingQuestionMeta) else 0
    title_text = str(q.title or "").strip()
    forced_option_text = (
        q.forced_option_text if isinstance(q, ChoiceQuestionMeta) and q.forced_option_text else ""
    )
    attached_option_selects = (
        q.attached_option_selects
        if isinstance(q, ChoiceQuestionMeta) and isinstance(q.attached_option_selects, list)
        else []
    )
    provider_question_id = str(q.provider_question_id or "").strip()
    provider_page_id = str(q.provider_page_id or "").strip()

    q_type = infer_question_entry_type(q)

    base_option_count = max(option_count, rating_max, 1)
    if q_type in TEXT_TYPES:
        option_count = max(base_option_count, text_inputs, 1)
    else:
        option_count = base_option_count

    forced_option_index = _normalize_forced_option_index(
        q.forced_option_index if isinstance(q, ChoiceQuestionMeta) else None,
        option_count,
    )
    parsed_title_key = _normalize_title(title_text)

    return _QuestionAttrs(
        num=q.num,
        option_count=option_count,
        rows=rows,
        text_inputs=text_inputs,
        slider_min=slider_min,
        slider_max=slider_max,
        title_text=title_text,
        forced_option_text=forced_option_text,
        forced_option_index=forced_option_index,
        attached_option_selects=list(attached_option_selects),
        provider_question_id=provider_question_id,
        provider_page_id=provider_page_id,
        q_type=QuestionType(q_type) if isinstance(q_type, str) else q_type,
        parsed_title_key=parsed_title_key,
    )


def _find_matching_config(
    attrs: _QuestionAttrs,
    existing_by_provider: dict[tuple[str, str], QuestionInfo],
    existing_by_num: dict[int, QuestionInfo],
    existing_by_title: dict[str, QuestionInfo],
) -> QuestionInfo | None:
    """Locate the best matching existing entry (by provider key, question num, or title)."""
    existing_config: QuestionInfo | None = None
    provider_key = _normalize_provider_key(
        attrs.provider_question_id or "wjx", attrs.provider_question_id
    )
    if provider_key:
        candidate = existing_by_provider.get(provider_key)
        if candidate and candidate.question_type == str(attrs.q_type):
            existing_config = candidate
    parsed_question_num = _normalize_question_num(attrs.num)
    if existing_config is None and parsed_question_num is not None:
        candidate = existing_by_num.get(parsed_question_num)
        if candidate and candidate.question_type == str(attrs.q_type):
            candidate_title_key = _normalize_title(candidate.title)
            if (
                attrs.parsed_title_key
                and candidate_title_key
                and candidate_title_key != attrs.parsed_title_key
            ):
                candidate = None
            if candidate is not None:
                existing_config = candidate
    if existing_config is None and attrs.parsed_title_key:
        candidate = existing_by_title.get(attrs.parsed_title_key)
        if candidate and candidate.question_type == str(attrs.q_type):
            existing_config = candidate
    return existing_config


def _resolve_config_from_existing(
    existing_config: QuestionInfo,
    q_type: QuestionType,
) -> _ResolvedConfig:
    """Deep-copy configuration values from an existing *QuestionInfo*."""
    detail = existing_config.details
    answer_cfg = detail.answer_config
    text_cfg = answer_cfg if isinstance(answer_cfg, TextQuestionAnswerConfig) else None
    multi_cfg = answer_cfg if isinstance(answer_cfg, MultiTextQuestionAnswerConfig) else None
    choice_cfg = answer_cfg if isinstance(answer_cfg, ChoiceQuestionAnswerConfig) else None
    location_cfg = answer_cfg if isinstance(answer_cfg, LocationQuestionAnswerConfig) else None
    return _ResolvedConfig(
        probabilities=copy.deepcopy(detail.probabilities),
        distribution=detail.distribution_mode or "random",
        custom_weights=copy.deepcopy(detail.custom_weights),
        option_count=0,  # caller fills in from attrs
        ai_enabled=answer_cfg.ai_enabled if q_type in TEXT_TYPES else False,
        text_random_mode=(
            str(text_cfg.text_random_mode or "none") if text_cfg is not None else "none"
        ),
        text_random_int_range=cast(
            list[object],
            copy.deepcopy(text_cfg.text_random_int_range) if text_cfg is not None else [],
        ),
        multi_text_blank_modes=(
            copy.deepcopy(multi_cfg.multi_text_blank_modes) if multi_cfg is not None else []
        ),
        multi_text_blank_ai_flags=(
            copy.deepcopy(multi_cfg.multi_text_blank_ai_flags) if multi_cfg is not None else []
        ),
        multi_text_blank_int_ranges=(
            copy.deepcopy(multi_cfg.multi_text_blank_int_ranges) if multi_cfg is not None else []
        ),
        option_fill_texts=(
            copy.deepcopy(choice_cfg.option_fill_texts) if choice_cfg is not None else None
        ),
        fillable_indices=(
            copy.deepcopy(choice_cfg.fillable_option_indices) if choice_cfg is not None else None
        ),
        attached_selects=cast(
            list[object],
            copy.deepcopy(choice_cfg.attached_option_selects or []) if choice_cfg is not None else [],
        ),
        location_parts=(
            list(location_cfg.location_parts) if location_cfg is not None else []
        ),
    )


# Maps each question type to a resolver returning
# (probabilities, distribution, custom_weights, option_count).
_DefaultConfigTuple = tuple[object, str, object, int]
_DefaultConfigResolver = Callable[["_QuestionAttrs", int], _DefaultConfigTuple]

_DEFAULT_CONFIG_DISPATCH: dict[QuestionType, _DefaultConfigResolver] = {
    QuestionType.SINGLE: lambda _attrs, option_count: (-1, "random", None, option_count),
    QuestionType.DROPDOWN: lambda _attrs, option_count: (-1, "random", None, option_count),
    QuestionType.SCALE: lambda _attrs, option_count: (-1, "random", None, option_count),
    QuestionType.MULTIPLE: (
        lambda _attrs, option_count: (
            [DEFAULT_MULTIPLE_PROBABILITY] * option_count,
            "random",
            None,
            option_count,
        )
    ),
    QuestionType.MATRIX: lambda _attrs, option_count: (-1, "random", None, option_count),
    QuestionType.ORDER: lambda _attrs, option_count: (-1, "random", None, option_count),
    QuestionType.SCORE: (
        lambda _attrs, option_count: (
            list(weights := _build_mid_bias_weights(max(option_count, 2))),
            "custom",
            list(weights),
            max(option_count, 2),
        )
    ),
    QuestionType.SLIDER: (
        lambda attrs, option_count: _resolve_slider_default_config(attrs)
    ),
}


def _resolve_fallback_default_config(
    attrs: "_QuestionAttrs", option_count: int
) -> _DefaultConfigTuple:
    return ([1.0], "random", None, option_count)


_DEFAULT_CONFIG_DISPATCH_FALLBACK: _DefaultConfigResolver = _resolve_fallback_default_config


def _resolve_slider_default_config(attrs: "_QuestionAttrs") -> _DefaultConfigTuple:
    min_val = float(attrs.slider_min) if isinstance(attrs.slider_min, (int, float, str)) else 0.0
    max_val_raw = attrs.slider_max
    if isinstance(max_val_raw, (int, float, str)):
        max_val = float(max_val_raw)
    else:
        max_val = float(DEFAULT_SLIDER_MAX)
    if max_val <= min_val:
        max_val = min_val + DEFAULT_SLIDER_MAX
    midpoint = min_val + (max_val - min_val) / 2.0
    return ([midpoint], "custom", [midpoint], 1)


def _resolve_default_config(
    q: SurveyQuestionMeta,
    attrs: _QuestionAttrs,
) -> _ResolvedConfig:
    """Compute fresh default configuration values when no existing entry matches."""
    q_type = attrs.q_type
    option_count = attrs.option_count

    probabilities, distribution, custom_weights, option_count = _DEFAULT_CONFIG_DISPATCH.get(
        q_type, _DEFAULT_CONFIG_DISPATCH_FALLBACK
    )(attrs, option_count)

    multi_text_blank_modes = (
        _infer_multi_text_blank_modes(q, attrs.text_inputs)
        if q_type == QuestionType.MULTI_TEXT
        else []
    )

    return _ResolvedConfig(
        probabilities=probabilities,
        distribution=distribution,
        custom_weights=custom_weights,
        option_count=option_count,
        ai_enabled=False,
        text_random_mode="none",
        text_random_int_range=[],
        multi_text_blank_modes=multi_text_blank_modes,
        multi_text_blank_ai_flags=[],
        multi_text_blank_int_ranges=[],
        option_fill_texts=None,
        fillable_indices=None,
        attached_selects=[],
        location_parts=[],
    )


def _apply_forced_option_overrides(
    q: SurveyQuestionMeta,
    attrs: _QuestionAttrs,
    config: _ResolvedConfig,
) -> None:
    """Override probabilities/weights when the question has a forced-answer directive."""
    forced_option_index = attrs.forced_option_index
    if forced_option_index is None or attrs.q_type not in CHOICE_TYPES:
        return
    option_count = config.option_count
    if attrs.q_type == QuestionType.SCORE:
        option_count = max(option_count, 2)
        forced_option_index = min(forced_option_index, option_count - 1)
    forced_weights = _build_forced_single_weights(option_count, forced_option_index)
    config.probabilities = list(forced_weights)
    config.distribution = "custom"
    config.custom_weights = list(forced_weights)
    config.option_count = option_count
    logger.info(
        f"题号{q.num}检测到指定作答指令，已强制锁定为第{forced_option_index + 1}项（{attrs.forced_option_text or '无文本'}）"
    )


def _assemble_question_info(
    q: SurveyQuestionMeta,
    attrs: _QuestionAttrs,
    config: _ResolvedConfig,
    existing_config: QuestionInfo | None,
) -> QuestionInfo:
    """Build fillable-option data and construct the final *QuestionInfo*."""
    option_count = config.option_count

    fillable_option_indices = (
        normalize_fillable_indices(
            q.fillable_options if isinstance(q, ChoiceQuestionMeta) else None,
            option_count,
            config.fillable_indices,
        )
        if attrs.q_type in CHOICE_LIKE_TYPES
        else []
    )
    option_fill_texts = (
        _filter_option_fill_texts_to_fillable(
            config.option_fill_texts,
            option_count,
            fillable_option_indices,
        )
        if attrs.q_type in CHOICE_LIKE_TYPES
        else None
    )

    answer_config_cls = answer_config_type_for_question_type(
        attrs.q_type,
        location_parts=config.location_parts if config.location_parts else None,
    )

    answer_config_kwargs: dict[str, Any] = dict(ai_enabled=config.ai_enabled)

    if answer_config_cls is ChoiceQuestionAnswerConfig:
        answer_config_kwargs["option_fill_texts"] = option_fill_texts
        answer_config_kwargs["fillable_option_indices"] = fillable_option_indices
        answer_config_kwargs["attached_option_selects"] = (
            normalize_attached_selects(
                attrs.attached_option_selects,
                config.attached_selects
                if attrs.q_type in (QuestionType.SINGLE, QuestionType.MULTIPLE)
                else None,
            )
            if attrs.q_type in (QuestionType.SINGLE, QuestionType.MULTIPLE)
            else []
        )
    elif answer_config_cls is TextQuestionAnswerConfig:
        answer_config_kwargs["text_random_mode"] = (
            config.text_random_mode if attrs.q_type == QuestionType.TEXT else "none"
        )
        answer_config_kwargs["text_random_int_range"] = cast(
            Any, config.text_random_int_range
        )
    elif answer_config_cls is MultiTextQuestionAnswerConfig:
        answer_config_kwargs["multi_text_blank_modes"] = config.multi_text_blank_modes
        answer_config_kwargs["multi_text_blank_ai_flags"] = config.multi_text_blank_ai_flags
        answer_config_kwargs["multi_text_blank_int_ranges"] = config.multi_text_blank_int_ranges
    elif answer_config_cls is LocationQuestionAnswerConfig:
        answer_config_kwargs["location_parts"] = (
            list(existing_config.details.answer_config.location_parts)  # type: ignore[union-attr]
            if existing_config
            and isinstance(existing_config.details.answer_config, LocationQuestionAnswerConfig)
            else []
        )

    answer_config = answer_config_cls(**answer_config_kwargs)

    detail = QuestionDetail(
        provider_question_id=attrs.provider_question_id or None,
        provider_page_id=attrs.provider_page_id or None,
        probabilities=cast(Any, config.probabilities),
        distribution_mode=config.distribution,
        custom_weights=cast(Any, config.custom_weights),
        answer_config=answer_config,
    )

    return QuestionInfo(
        num=q.num,
        title=attrs.title_text or "",
        question_type=str(attrs.q_type),
        options=cast(list[str], q.option_texts if isinstance(q, ChoiceQuestionMeta) and q.option_texts else []),
        required=getattr(q, "required", False),
        details=detail,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_default_survey_questions(
    questions_info: list[SurveyQuestionMeta],
    *,
    survey_url: str = "",
    existing_entries: list[QuestionInfo] | None = None,
) -> list[QuestionInfo]:

    existing_by_num, existing_by_title, existing_by_provider = _build_existing_entry_maps(
        existing_entries
    )

    entries: list[QuestionInfo] = []
    for q in questions_info:
        if q.type_code == TypeCode.DESCRIPTION or q.unsupported:
            continue

        attrs = _extract_question_attrs(q)

        existing_config = _find_matching_config(
            attrs,
            existing_by_provider,
            existing_by_num,
            existing_by_title,
        )

        if existing_config:
            config = _resolve_config_from_existing(existing_config, attrs.q_type)
            config.option_count = attrs.option_count
        else:
            config = _resolve_default_config(q, attrs)

        _apply_forced_option_overrides(q, attrs, config)

        entries.append(_assemble_question_info(q, attrs, config, existing_config))
    return entries
