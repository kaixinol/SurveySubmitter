from __future__ import annotations

import copy
import logging
from typing import Any

from survey_submitter.constants import DEFAULT_FILL_TEXT
from survey_submitter.core.questions.meta_helpers import (
    infer_question_entry_type,
    normalize_attached_option_selects,
    normalize_fillable_option_indices,
)
from survey_submitter.core.questions.schema import QuestionEntry
from survey_submitter.core.questions.schema import _TEXT_RANDOM_ID_CARD, _TEXT_RANDOM_MOBILE, _TEXT_RANDOM_NAME, _TEXT_RANDOM_NONE
from survey_submitter.core.questions.types import QuestionType, TypeCode, CHOICE_TYPES, TEXT_TYPES, CHOICE_LIKE_TYPES
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

__all__ = ["build_default_question_entries"]

DEFAULT_MULTIPLE_PROBABILITY = 50.0
DEFAULT_SLIDER_MAX = 100.0


def _as_float(val: Any, default: float) -> float:
    try:
        return float(val)
    except Exception:
        return default


def _build_mid_bias_weights(option_count: int) -> list[float]:
    count = max(1, int(option_count or 1))
    return [1.0] * count


def _normalize_question_num(raw: Any) -> int | None:
    try:
        if raw is None:
            return None
        return int(raw)
    except Exception:
        return None


def _normalize_title(raw: Any) -> str:
    try:
        text = str(raw or "").strip()
    except Exception:
        return ""
    if not text:
        return ""
    return "".join(text.split())


def _normalize_provider_key(raw_provider: Any, raw_question_id: Any) -> tuple[str, str] | None:
    provider = normalize_survey_provider(raw_provider, default=SURVEY_PROVIDER_WJX)
    question_id = str(raw_question_id or "").strip()
    if not question_id:
        return None
    return provider, question_id


def _normalize_forced_option_index(raw: Any, option_count: int) -> int | None:
    try:
        idx = int(raw)
    except Exception:
        return None
    total = max(0, int(option_count or 0))
    if 0 <= idx < total:
        return idx
    return None


def _build_forced_single_weights(option_count: int, forced_index: int) -> list[float]:
    total = max(1, int(option_count or 1))
    return [1.0 if idx == forced_index else 0.0 for idx in range(total)]


def _infer_multi_text_blank_modes(q: SurveyQuestionMeta, blank_count: int) -> list[str]:
    labels = [str(item or "").strip() for item in list(q.text_input_labels if isinstance(q, TextQuestionMeta) and q.text_input_labels else [])]
    title = str(q.title or "").strip()
    modes: list[str] = []
    for index in range(max(0, int(blank_count or 0))):
        text = labels[index] if index < len(labels) else ""
        if not text and blank_count <= 1:
            text = title
        normalized = "".join(str(text or "").split()).lower()
        if any(marker in normalized for marker in ("手机号", "手机号码", "手机", "电话", "联系电话", "联系方式")):
            modes.append(_TEXT_RANDOM_MOBILE)
        elif any(marker in normalized for marker in ("身份证", "证件号", "证件号码")):
            modes.append(_TEXT_RANDOM_ID_CARD)
        elif any(marker in normalized for marker in ("姓名", "名字", "联系人")):
            modes.append(_TEXT_RANDOM_NAME)
        else:
            modes.append(_TEXT_RANDOM_NONE)
    return modes


def _filter_option_fill_texts_to_fillable(
    option_fill_texts: Any,
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
        except Exception:
            continue
        if 0 <= option_index < total:
            fillable_set.add(option_index)
    if not fillable_set:
        return None
    normalized: list[str | None] = []
    for option_index in range(total):
        raw_value = option_fill_texts[option_index] if option_index < len(option_fill_texts) else None
        text = str(raw_value or "").strip()
        normalized.append(text if option_index in fillable_set and text else None)
    return normalized if any(normalized) else None


def build_default_question_entries(
    questions_info: list[SurveyQuestionMeta],
    *,
    survey_url: str = "",
    existing_entries: list[QuestionEntry] | None = None,
) -> list[QuestionEntry]:
    

    existing_by_num: dict[int, QuestionEntry] = {}
    existing_by_title: dict[str, QuestionEntry] = {}
    existing_by_provider: dict[tuple[str, str], QuestionEntry] = {}
    if existing_entries:
        for entry in existing_entries:
            q_num = _normalize_question_num(entry.question_num)
            if q_num is not None and q_num not in existing_by_num:
                existing_by_num[q_num] = entry
            title_key = _normalize_title(entry.question_title)
            if title_key and title_key not in existing_by_title:
                existing_by_title[title_key] = entry
            provider_key = _normalize_provider_key(
                entry.survey_provider,
                entry.provider_question_id,
            )
            if provider_key and provider_key not in existing_by_provider:
                existing_by_provider[provider_key] = entry

    detected_provider = detect_survey_provider(survey_url)
    entries: list[QuestionEntry] = []
    for q in questions_info:
        if q.type_code == TypeCode.DESCRIPTION or q.unsupported:
            continue

        option_texts = q.option_texts if isinstance(q, ChoiceQuestionMeta) and q.option_texts else []
        option_count = len(option_texts)
        rows = q.rows if isinstance(q, MatrixQuestionMeta) else 1
        is_location = q.is_location if isinstance(q, TextQuestionMeta) else False
        text_inputs = q.text_inputs if isinstance(q, TextQuestionMeta) else 0
        slider_min = q.slider_min if isinstance(q, SliderQuestionMeta) else None
        slider_max = q.slider_max if isinstance(q, SliderQuestionMeta) else None
        rating_max = q.rating_max if isinstance(q, RatingQuestionMeta) else 0
        title_text = str(q.title or "").strip()
        forced_option_text = q.forced_option_text if isinstance(q, ChoiceQuestionMeta) and q.forced_option_text else ""
        attached_option_selects = (
            q.attached_option_selects
            if isinstance(q, ChoiceQuestionMeta) and isinstance(q.attached_option_selects, list)
            else []
        )
        survey_provider = detected_provider
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

        existing_config: QuestionEntry | None = None
        provider_key = _normalize_provider_key(survey_provider, provider_question_id)
        if provider_key:
            candidate = existing_by_provider.get(provider_key)
            if candidate and candidate.question_type == q_type:
                existing_config = candidate
        parsed_question_num = _normalize_question_num(q.num)
        if existing_config is None and parsed_question_num is not None:
            candidate = existing_by_num.get(parsed_question_num)
            if candidate and candidate.question_type == q_type:
                candidate_title_key = _normalize_title(candidate.question_title)
                if parsed_title_key and candidate_title_key and candidate_title_key != parsed_title_key:
                    candidate = None
                if candidate is not None:
                    existing_config = candidate
        if existing_config is None and parsed_title_key:
            candidate = existing_by_title.get(parsed_title_key)
            if candidate and candidate.question_type == q_type:
                existing_config = candidate

        if existing_config:
            probabilities: Any = copy.deepcopy(existing_config.probabilities)
            distribution = existing_config.distribution_mode or "random"
            custom_weights = copy.deepcopy(existing_config.custom_weights)
            texts = copy.deepcopy(existing_config.texts)
            ai_enabled_from_existing = existing_config.ai_enabled if q_type in TEXT_TYPES else False
            text_random_mode_from_existing = (
                str(existing_config.text_random_mode or "none")
                if q_type == QuestionType.TEXT
                else "none"
            )
            text_random_int_range_from_existing = (
                copy.deepcopy(existing_config.text_random_int_range)
                if q_type == QuestionType.TEXT
                else []
            )
            multi_text_blank_modes_from_existing = (
                copy.deepcopy(existing_config.multi_text_blank_modes)
                if q_type == QuestionType.MULTI_TEXT
                else []
            )
            multi_text_blank_ai_flags_from_existing = (
                copy.deepcopy(existing_config.multi_text_blank_ai_flags)
                if q_type == QuestionType.MULTI_TEXT
                else []
            )
            multi_text_blank_int_ranges_from_existing = (
                copy.deepcopy(existing_config.multi_text_blank_int_ranges)
                if q_type == QuestionType.MULTI_TEXT
                else []
            )
            option_fill_texts_from_existing = (
                copy.deepcopy(existing_config.option_fill_texts)
                if q_type in CHOICE_LIKE_TYPES
                else None
            )
            fillable_indices_from_existing = (
                copy.deepcopy(existing_config.fillable_option_indices)
                if q_type in CHOICE_LIKE_TYPES
                else None
            )
            attached_selects_from_existing = copy.deepcopy(existing_config.attached_option_selects or [])
        else:
            ai_enabled_from_existing = False
            text_random_mode_from_existing = "none"
            text_random_int_range_from_existing = []
            multi_text_blank_modes_from_existing = (
                _infer_multi_text_blank_modes(q, text_inputs)
                if q_type == QuestionType.MULTI_TEXT
                else []
            )
            multi_text_blank_ai_flags_from_existing = []
            multi_text_blank_int_ranges_from_existing = []
            option_fill_texts_from_existing = None
            fillable_indices_from_existing = None
            attached_selects_from_existing = []
            if q_type in {QuestionType.SINGLE, QuestionType.DROPDOWN, QuestionType.SCALE}:
                probabilities = -1
                distribution = "random"
                custom_weights = None
                texts = None
            elif q_type == QuestionType.SCORE:
                option_count = max(option_count, 2)
                weights = _build_mid_bias_weights(option_count)
                probabilities = list(weights)
                distribution = "custom"
                custom_weights = list(weights)
                texts = None
            elif q_type == QuestionType.MULTIPLE:
                probabilities = [DEFAULT_MULTIPLE_PROBABILITY] * option_count
                distribution = "random"
                custom_weights = None
                texts = None
            elif q_type == QuestionType.MATRIX:
                probabilities = -1
                distribution = "random"
                custom_weights = None
                texts = None
            elif q_type == QuestionType.ORDER:
                probabilities = -1
                distribution = "random"
                custom_weights = None
                texts = None
            elif q_type == QuestionType.SLIDER:
                min_val = _as_float(slider_min, 0.0)
                max_val = _as_float(slider_max, DEFAULT_SLIDER_MAX if slider_max is None else slider_max)
                if max_val <= min_val:
                    max_val = min_val + DEFAULT_SLIDER_MAX
                midpoint = min_val + (max_val - min_val) / 2.0
                probabilities = [midpoint]
                distribution = "custom"
                custom_weights = [midpoint]
                texts = None
                option_count = 1
            else:
                probabilities = [1.0]
                distribution = "random"
                custom_weights = None
                texts = [DEFAULT_FILL_TEXT]

        if forced_option_index is not None and q_type in CHOICE_TYPES:
            if q_type == QuestionType.SCORE:
                option_count = max(option_count, 2)
                forced_option_index = min(forced_option_index, option_count - 1)
            forced_weights = _build_forced_single_weights(option_count, forced_option_index)
            probabilities = list(forced_weights)
            distribution = "custom"
            custom_weights = list(forced_weights)
            logging.info(
                "题号%s检测到指定作答指令，已强制锁定为第%s项（%s）",
                q.num,
                forced_option_index + 1,
                forced_option_text or "无文本",
            )

        fillable_option_indices = (
            normalize_fillable_option_indices(
                q.fillable_options if isinstance(q, ChoiceQuestionMeta) else None,
                option_count,
                fillable_indices_from_existing,
            )
            if q_type in CHOICE_LIKE_TYPES
            else []
        )
        option_fill_texts = (
            _filter_option_fill_texts_to_fillable(
                option_fill_texts_from_existing,
                option_count,
                fillable_option_indices,
            )
            if q_type in CHOICE_LIKE_TYPES
            else None
        )
        entries.append(
            QuestionEntry(
                question_type=q_type,
                probabilities=probabilities,
                texts=texts,
                rows=rows,
                option_count=option_count,
                distribution_mode=distribution,
                custom_weights=custom_weights,
                question_num=q.num,
                question_title=title_text or None,
                survey_provider=survey_provider,
                provider_question_id=provider_question_id or None,
                provider_page_id=provider_page_id or None,
                ai_enabled=ai_enabled_from_existing if q_type in TEXT_TYPES else False,
                multi_text_blank_modes=multi_text_blank_modes_from_existing if q_type == QuestionType.MULTI_TEXT else [],
                multi_text_blank_ai_flags=multi_text_blank_ai_flags_from_existing if q_type == QuestionType.MULTI_TEXT else [],
                multi_text_blank_int_ranges=multi_text_blank_int_ranges_from_existing if q_type == QuestionType.MULTI_TEXT else [],
                text_random_mode=text_random_mode_from_existing if q_type == QuestionType.TEXT else "none",
                text_random_int_range=text_random_int_range_from_existing if q_type == QuestionType.TEXT else [],
                option_fill_texts=option_fill_texts,
                fillable_option_indices=fillable_option_indices,
                attached_option_selects=(
                    normalize_attached_option_selects(
                        attached_option_selects,
                        attached_selects_from_existing if q_type == QuestionType.SINGLE else None,
                    )
                    if q_type == QuestionType.SINGLE
                    else []
                ),
                is_location=is_location,
                location_parts=list(existing_config.location_parts or []) if existing_config else [],
            )
        )
    return entries
