from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from survey_submitter.constants import DEFAULT_FILL_TEXT, DIMENSION_UNGROUPED
from survey_submitter.core.psychometrics.ordinal_options import infer_ordinal_option_mapping
from survey_submitter.core.questions.schema import (
    GLOBAL_RELIABILITY_DIMENSION,
    QuestionEntry,
    _TEXT_RANDOM_ID_CARD,
    _TEXT_RANDOM_ID_CARD_TOKEN,
    _TEXT_RANDOM_INTEGER,
    _TEXT_RANDOM_MOBILE,
    _TEXT_RANDOM_MOBILE_TOKEN,
    _TEXT_RANDOM_NAME,
    _TEXT_RANDOM_NAME_TOKEN,
    _TEXT_RANDOM_NONE,
    _infer_option_count,
)
from survey_submitter.core.questions.meta_helpers import (
    count_positive_weights,
    find_all_zero_attached_selects,
    find_all_zero_matrix_rows,
)
from survey_submitter.core.questions.strict_ratio import is_strict_custom_ratio_mode
from survey_submitter.core.questions.utils import (
    build_random_int_token,
    normalize_option_fill_texts as _normalize_option_fill_texts,
    normalize_probabilities,
    normalize_single_like_prob_config as _normalize_single_like_prob_config,
    resolve_prob_config as _resolve_prob_config,
    serialize_random_int_range,
    try_parse_random_int_range,
)
from survey_submitter.core.questions.types import QuestionType, CHOICE_TYPES, TEXT_TYPES, RATING_TYPES
from survey_submitter.providers.common import make_provider_question_key

DEFAULT_SLIDER_TARGET = 50.0

if TYPE_CHECKING:
    from survey_submitter.core.task import ExecutionConfig

__all__ = ["configure_probabilities"]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _raise_if_all_zero_single_like(raw_weights: Any, question_num: int, question_type: str) -> None:
    if isinstance(raw_weights, list) and raw_weights and count_positive_weights(raw_weights) <= 0:
        raise ValueError(
            f"第 {question_num} 题（{question_type}）配置无效：所有选项配比均为 0，请至少保留一个大于 0 的选项。"
        )


def _raise_if_all_zero_matrix(raw_weights: Any, question_num: int) -> None:
    invalid_rows = find_all_zero_matrix_rows(raw_weights)
    if not invalid_rows:
        return
    if invalid_rows == [0]:
        raise ValueError(
            f"第 {question_num} 题（矩阵题）配置无效：所有选项配比均为 0，请至少保留一个大于 0 的选项。"
        )
    raise ValueError(
        f"第 {question_num} 题（矩阵题）配置无效：第 {invalid_rows[0]} 行配比全部为 0，请至少保留一个大于 0 的选项。"
    )


def _raise_if_all_zero_attached_selects(entry: QuestionEntry, question_num: int) -> None:
    issues = find_all_zero_attached_selects(entry.attached_option_selects or [])
    if not issues:
        return
    cfg_idx, option_text = issues[0]
    raise ValueError(
        f"第 {question_num} 题（嵌入式下拉）配置无效：第 {cfg_idx} 组（{option_text or '未命名选项'}）配比全部为 0，请至少保留一个大于 0 的选项。"
    )


# ---------------------------------------------------------------------------
# Target initialization
# ---------------------------------------------------------------------------


def _init_target_collections(target: "ExecutionConfig") -> None:
    target.single_prob = []
    target.droplist_prob = []
    target.multiple_prob = []
    target.matrix_prob = []
    target.scale_prob = []
    target.slider_targets = []
    target.texts = []
    target.texts_prob = []
    target.text_entry_types = []
    target.text_ai_flags = []
    target.text_titles = []
    target.location_parts = {}
    target.multi_text_blank_modes = []
    target.multi_text_blank_ai_flags = []
    target.multi_text_blank_int_ranges = []
    target.single_option_fill_texts = []
    target.single_attached_option_selects = []
    target.droplist_option_fill_texts = []
    target.multiple_option_fill_texts = []
    target.question_config_index_map = {}
    target.provider_question_config_index_map = {}
    target.question_dimension_map = {}
    target.question_ordinal_score_map = {}
    target.question_strict_ratio_map = {}
    target.question_psycho_bias_map = {}


# ---------------------------------------------------------------------------
# Provider mapping helper
# ---------------------------------------------------------------------------


def _remember_provider_mapping(
    target: "ExecutionConfig",
    entry: QuestionEntry,
    mapped_value: tuple[str, int],
) -> None:
    provider_key = make_provider_question_key(
        entry.survey_provider,
        entry.provider_page_id,
        entry.provider_question_id,
    )
    if provider_key:
        target.provider_question_config_index_map[provider_key] = mapped_value


# ---------------------------------------------------------------------------
# Runtime dimension helper
# ---------------------------------------------------------------------------


def _resolve_runtime_dimension(
    entry: QuestionEntry,
    *,
    reliability_mode_enabled: bool,
    strict_ratio: bool,
    allows_reliability: bool | None = None,
) -> str | None:
    allows_joint_ratio = (
        bool(allows_reliability)
        if allows_reliability is not None
        else True
    )
    if not reliability_mode_enabled or (strict_ratio and not allows_joint_ratio):
        return None
    raw_dimension = str(entry.dimension or "").strip()
    if not raw_dimension or raw_dimension == DIMENSION_UNGROUPED:
        return None
    return raw_dimension


# ---------------------------------------------------------------------------
# Per-question-type handlers
# ---------------------------------------------------------------------------


def _handle_single(
    entry: QuestionEntry,
    question_num: int,
    probs: Any,
    strict_ratio: bool,
    target: "ExecutionConfig",
    idx: int,
    reliability_mode_enabled: bool,
    reliability_candidates: list[tuple[int, bool, str]],
) -> int:
    _raise_if_all_zero_single_like(probs, question_num, "single")
    mapped_value = ("single", idx)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    raw_meta = getattr(target, "questions_metadata", {}).get(question_num) if hasattr(target, "questions_metadata") else None
    option_texts = list(getattr(raw_meta, "option_texts", []) or [])
    ordinal_mapping = infer_ordinal_option_mapping(option_texts)
    is_ordinal_single = ordinal_mapping is not None and ordinal_mapping.option_count == max(1, entry.option_count)
    if is_ordinal_single and ordinal_mapping is not None:
        target.question_ordinal_score_map[question_num] = list(ordinal_mapping.score_by_choice_index)
        target.question_dimension_map[question_num] = _resolve_runtime_dimension(
            entry,
            reliability_mode_enabled=reliability_mode_enabled,
            strict_ratio=strict_ratio,
            allows_reliability=True,
        )
        target.question_psycho_bias_map[question_num] = str(entry.psycho_bias or "custom")
        reliability_candidates.append((question_num, strict_ratio, entry.question_type))
    idx += 1
    target.single_prob.append(_normalize_single_like_prob_config(probs, entry.option_count))
    target.single_option_fill_texts.append(_normalize_option_fill_texts(entry.option_fill_texts, entry.option_count))
    target.single_attached_option_selects.append(copy.deepcopy(entry.attached_option_selects or []))
    return idx


def _handle_dropdown(
    entry: QuestionEntry,
    question_num: int,
    probs: Any,
    strict_ratio: bool,
    target: "ExecutionConfig",
    idx: int,
    reliability_mode_enabled: bool,
    reliability_candidates: list[tuple[int, bool, str]],
) -> int:
    _raise_if_all_zero_single_like(probs, question_num, "dropdown")
    mapped_value = ("dropdown", idx)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    target.question_dimension_map[question_num] = _resolve_runtime_dimension(
        entry,
        reliability_mode_enabled=reliability_mode_enabled,
        strict_ratio=strict_ratio,
    )
    target.question_psycho_bias_map[question_num] = str(entry.psycho_bias or "custom")
    reliability_candidates.append((question_num, strict_ratio, entry.question_type))
    idx += 1
    target.droplist_prob.append(_normalize_single_like_prob_config(probs, entry.option_count))
    target.droplist_option_fill_texts.append(_normalize_option_fill_texts(entry.option_fill_texts, entry.option_count))
    return idx


def _handle_multiple(
    entry: QuestionEntry,
    question_num: int,
    probs: Any,
    target: "ExecutionConfig",
    idx: int,
) -> int:
    mapped_value = ("multiple", idx)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    idx += 1
    if not isinstance(probs, list):
        raise ValueError("多选题必须提供概率列表，数值范围0-100")
    target.multiple_prob.append([float(value) for value in probs])
    target.multiple_option_fill_texts.append(_normalize_option_fill_texts(entry.option_fill_texts, entry.option_count))
    return idx


def _normalize_matrix_row(raw_row: Any, option_count: int) -> list[float] | None:
    if not isinstance(raw_row, (list, tuple)):
        return None
    cleaned: list[float] = []
    for value in raw_row:
        try:
            cleaned.append(max(0.0, float(value)))
        except (ValueError, TypeError):
            continue
    if not cleaned:
        return None
    if len(cleaned) < option_count:
        cleaned = cleaned + [1.0] * (option_count - len(cleaned))
    elif len(cleaned) > option_count:
        cleaned = cleaned[:option_count]
    try:
        return normalize_probabilities(cleaned)
    except ValueError:
        return None


def _handle_matrix(
    entry: QuestionEntry,
    question_num: int,
    probs: Any,
    strict_ratio: bool,
    target: "ExecutionConfig",
    idx: int,
    reliability_mode_enabled: bool,
    reliability_candidates: list[tuple[int, bool, str]],
) -> int:
    _raise_if_all_zero_matrix(probs, question_num)
    rows = max(1, entry.rows)
    mapped_value = ("matrix", idx)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    target.question_dimension_map[question_num] = _resolve_runtime_dimension(
        entry,
        reliability_mode_enabled=reliability_mode_enabled,
        strict_ratio=strict_ratio,
    )
    bias_value = entry.psycho_bias
    target.question_psycho_bias_map[question_num] = list(bias_value) if isinstance(bias_value, list) else str(bias_value or "custom")
    reliability_candidates.append((question_num, strict_ratio, entry.question_type))
    idx += rows
    option_count = max(1, _infer_option_count(entry))

    row_weights_source: list[Any] | None = None
    if isinstance(probs, list) and any(isinstance(item, (list, tuple)) for item in probs):
        row_weights_source = probs
    elif isinstance(entry.custom_weights, list) and any(isinstance(item, (list, tuple)) for item in entry.custom_weights):
        row_weights_source = entry.custom_weights

    if row_weights_source is not None:
        last_row: Any | None = None
        for row_idx in range(rows):
            raw_row = row_weights_source[row_idx] if row_idx < len(row_weights_source) else last_row
            normalized_row = _normalize_matrix_row(raw_row, option_count)
            if normalized_row is None:
                normalized_row = [1.0 / option_count] * option_count
            target.matrix_prob.append(normalized_row)
            last_row = raw_row if raw_row is not None else last_row
    elif isinstance(probs, list):
        normalized = _normalize_matrix_row(probs, option_count)
        if normalized is None:
            normalized = [1.0 / option_count] * option_count
        for _ in range(rows):
            target.matrix_prob.append(list(normalized))
    else:
        for _ in range(rows):
            target.matrix_prob.append(-1)
    return idx


def _handle_scale(
    entry: QuestionEntry,
    question_num: int,
    probs: Any,
    strict_ratio: bool,
    target: "ExecutionConfig",
    idx: int,
    reliability_mode_enabled: bool,
    reliability_candidates: list[tuple[int, bool, str]],
) -> int:
    _raise_if_all_zero_single_like(probs, question_num, entry.question_type)
    mapped_value = (entry.question_type, idx)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    target.question_dimension_map[question_num] = _resolve_runtime_dimension(
        entry,
        reliability_mode_enabled=reliability_mode_enabled,
        strict_ratio=strict_ratio,
    )
    target.question_psycho_bias_map[question_num] = str(entry.psycho_bias or "custom")
    reliability_candidates.append((question_num, strict_ratio, entry.question_type))
    idx += 1
    target.scale_prob.append(_normalize_single_like_prob_config(probs, entry.option_count))
    return idx


def _handle_slider(
    entry: QuestionEntry,
    question_num: int,
    probs: Any,
    target: "ExecutionConfig",
    idx: int,
) -> tuple[int, bool]:
    """Returns (new_idx, should_continue)."""
    mapped_value = ("slider", idx)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    idx += 1
    mode = str(entry.distribution_mode or "").strip().lower()
    if mode == "random":
        target.slider_targets.append(float("nan"))
        return idx, True
    target_value: float | None = None
    if isinstance(entry.custom_weights, (list, tuple)) and entry.custom_weights:
        first = entry.custom_weights[0]
        target_value = float(first) if isinstance(first, (int, float)) else None
    if target_value is None:
        if isinstance(probs, (int, float)):
            target_value = float(probs)
        elif isinstance(probs, list) and probs:
            try:
                target_value = float(probs[0])
            except (ValueError, TypeError):
                target_value = None
    target.slider_targets.append(DEFAULT_SLIDER_TARGET if target_value is None else target_value)
    return idx, False


def _handle_order(
    entry: QuestionEntry,
    question_num: int,
    target: "ExecutionConfig",
) -> None:
    mapped_value = ("order", -1)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)


def _handle_location(
    entry: QuestionEntry,
    question_num: int,
    target: "ExecutionConfig",
) -> None:
    mapped_value = ("location", -1)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    target.location_parts[question_num] = [
        str(item or "").strip()
        for item in list(entry.location_parts or [])[:3]
    ]


def _handle_text(
    entry: QuestionEntry,
    question_num: int,
    probs: Any,
    target: "ExecutionConfig",
    idx_text: int,
) -> tuple[int, bool]:
    """Returns (new_idx_text, was_location)."""
    if not entry.is_location:
        mapped_value = ("text", idx_text)
        target.question_config_index_map[question_num] = mapped_value
        _remember_provider_mapping(target, entry, mapped_value)
        idx_text += 1
        is_location = False
    else:
        mapped_value = ("location", -1)
        target.question_config_index_map[question_num] = mapped_value
        _remember_provider_mapping(target, entry, mapped_value)
        target.location_parts[question_num] = [
            str(item or "").strip()
            for item in list(entry.location_parts or [])[:3]
        ]
        is_location = True

    text_random_mode = str(entry.text_random_mode or _TEXT_RANDOM_NONE).strip().lower()
    normalized_values = [str(item).strip() for item in (entry.texts or []) if str(item).strip()]
    normalized_blank_ai_flags: list[bool] = []
    normalized_blank_int_ranges: list[list[int]] = []
    if entry.question_type == QuestionType.MULTI_TEXT:
        raw_blank_ai_flags = entry.multi_text_blank_ai_flags or []
        if isinstance(raw_blank_ai_flags, list):
            normalized_blank_ai_flags = [bool(flag) for flag in raw_blank_ai_flags]
        raw_blank_int_ranges = entry.multi_text_blank_int_ranges or []
        if isinstance(raw_blank_int_ranges, list):
            normalized_blank_int_ranges = [serialize_random_int_range(item) for item in raw_blank_int_ranges]
        for blank_idx, mode in enumerate(entry.multi_text_blank_modes or []):
            if str(mode or _TEXT_RANDOM_NONE).strip().lower() != _TEXT_RANDOM_INTEGER:
                continue
            target_range = raw_blank_int_ranges[blank_idx] if blank_idx < len(raw_blank_int_ranges) else []
            if try_parse_random_int_range(target_range) is None:
                raise ValueError(f"多项填空题第{blank_idx + 1}个空位的随机整数范围未设置完整")
    if entry.question_type == QuestionType.TEXT:
        ai_enabled = bool(entry.ai_enabled)
    elif entry.question_type == QuestionType.MULTI_TEXT:
        ai_enabled = bool(entry.ai_enabled) or (
            bool(normalized_blank_ai_flags) and all(normalized_blank_ai_flags)
        )
    else:
        ai_enabled = False
    if entry.question_type == QuestionType.TEXT and text_random_mode in (_TEXT_RANDOM_NAME, _TEXT_RANDOM_MOBILE, _TEXT_RANDOM_ID_CARD, _TEXT_RANDOM_INTEGER):
        ai_enabled = False
        if text_random_mode == _TEXT_RANDOM_NAME:
            normalized_values = [_TEXT_RANDOM_NAME_TOKEN]
        elif text_random_mode == _TEXT_RANDOM_MOBILE:
            normalized_values = [_TEXT_RANDOM_MOBILE_TOKEN]
        elif text_random_mode == _TEXT_RANDOM_ID_CARD:
            normalized_values = [_TEXT_RANDOM_ID_CARD_TOKEN]
        else:
            text_random_range = serialize_random_int_range(entry.text_random_int_range)
            if len(text_random_range) != 2:
                raise ValueError("填空题随机整数范围未设置完整")
            normalized_values = [build_random_int_token(*text_random_range)]
    if not normalized_values:
        if ai_enabled:
            normalized_values = [DEFAULT_FILL_TEXT]
        else:
            raise ValueError("填空题至少需要一个候选答案")
    if isinstance(probs, list) and len(probs) == len(normalized_values):
        normalized = normalize_probabilities([float(value) for value in probs])
    else:
        normalized = normalize_probabilities([1.0] * len(normalized_values))
    target.texts.append(normalized_values)
    target.texts_prob.append(normalized)
    target.text_entry_types.append(entry.question_type)
    target.text_ai_flags.append(ai_enabled)
    target.text_titles.append(str(entry.question_title or ""))
    target.multi_text_blank_modes.append(entry.multi_text_blank_modes)
    target.multi_text_blank_ai_flags.append(normalized_blank_ai_flags)
    target.multi_text_blank_int_ranges.append(normalized_blank_int_ranges)
    return idx_text, is_location


# ---------------------------------------------------------------------------
# Post-loop reliability fallback
# ---------------------------------------------------------------------------


def _apply_reliability_fallback(
    target: "ExecutionConfig",
    reliability_mode_enabled: bool,
    reliability_candidates: list[tuple[int, bool, str]],
) -> None:
    has_explicit_runtime_dimension = any(
        isinstance(dimension, str) and bool(str(dimension).strip())
        for dimension in target.question_dimension_map.values()
    )
    if reliability_mode_enabled and reliability_candidates and not has_explicit_runtime_dimension:
        for question_num, strict_ratio, question_type in reliability_candidates:
            if strict_ratio or target.question_dimension_map.get(question_num):
                continue
            target.question_dimension_map[question_num] = GLOBAL_RELIABILITY_DIMENSION


# ---------------------------------------------------------------------------
# Main coordinator
# ---------------------------------------------------------------------------


def configure_probabilities(
    entries: list[QuestionEntry],
    ctx: "ExecutionConfig",
    reliability_mode_enabled: bool = True,
) -> None:
    target = ctx
    _init_target_collections(target)

    idx_single = idx_dropdown = idx_multiple = idx_matrix = idx_scale = idx_slider = idx_text = 0
    reliability_candidates: list[tuple[int, bool, str]] = []

    for idx, entry in enumerate(entries, start=1):
        question_num = entry.question_num if entry.question_num is not None else idx
        inferred_count = _infer_option_count(entry)
        if inferred_count and inferred_count != entry.option_count:
            entry.option_count = inferred_count
        probs = _resolve_prob_config(
            entry.probabilities,
            entry.custom_weights,
            prefer_custom=(entry.distribution_mode == "custom"),
        )
        _raise_if_all_zero_attached_selects(entry, question_num)
        strict_ratio = is_strict_custom_ratio_mode(
            entry.distribution_mode,
            probs,
            entry.custom_weights,
        )
        target.question_strict_ratio_map[question_num] = strict_ratio

        if entry.question_type == QuestionType.SINGLE:
            idx_single = _handle_single(
                entry, question_num, probs, strict_ratio, target, idx_single,
                reliability_mode_enabled, reliability_candidates,
            )
        elif entry.question_type == QuestionType.DROPDOWN:
            idx_dropdown = _handle_dropdown(
                entry, question_num, probs, strict_ratio, target, idx_dropdown,
                reliability_mode_enabled, reliability_candidates,
            )
        elif entry.question_type == QuestionType.MULTIPLE:
            idx_multiple = _handle_multiple(entry, question_num, probs, target, idx_multiple)
        elif entry.question_type == QuestionType.MATRIX:
            idx_matrix = _handle_matrix(
                entry, question_num, probs, strict_ratio, target, idx_matrix,
                reliability_mode_enabled, reliability_candidates,
            )
        elif entry.question_type in RATING_TYPES:
            idx_scale = _handle_scale(
                entry, question_num, probs, strict_ratio, target, idx_scale,
                reliability_mode_enabled, reliability_candidates,
            )
        elif entry.question_type == QuestionType.SLIDER:
            idx_slider, should_continue = _handle_slider(entry, question_num, probs, target, idx_slider)
            if should_continue:
                continue
        elif entry.question_type == QuestionType.ORDER:
            _handle_order(entry, question_num, target)
        elif entry.question_type == QuestionType.LOCATION:
            _handle_location(entry, question_num, target)
        elif entry.question_type in TEXT_TYPES:
            idx_text, _ = _handle_text(entry, question_num, probs, target, idx_text)

    _apply_reliability_fallback(target, reliability_mode_enabled, reliability_candidates)
