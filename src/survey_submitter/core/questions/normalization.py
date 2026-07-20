from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any, Callable, cast

from survey_submitter.constants import DEFAULT_FILL_TEXT, DIMENSION_UNGROUPED
from survey_submitter.core.questions.schema import (
    ChoiceQuestionEntry,
    GLOBAL_RELIABILITY_DIMENSION,
    LocationQuestionEntry,
    MultiTextQuestionEntry,
    QuestionEntry,
    TextQuestionEntry,
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
from survey_submitter.core.questions.types import (
    QuestionType,
)
from survey_submitter.providers.common import make_provider_question_key

DEFAULT_SLIDER_TARGET = 50.0

if TYPE_CHECKING:
    from survey_submitter.core.task import ExecutionConfig

__all__ = ["configure_probabilities"]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _raise_if_all_zero_single_like(
    raw_weights: object, question_num: int, question_type: str
) -> None:
    if isinstance(raw_weights, list) and raw_weights and count_positive_weights(raw_weights) <= 0:
        raise ValueError(
            f"第 {question_num} 题（{question_type}）配置无效：所有选项配比均为 0，请至少保留一个大于 0 的选项。"
        )


def _raise_if_all_zero_matrix(raw_weights: object, question_num: int) -> None:
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
    if not isinstance(entry, ChoiceQuestionEntry):
        return
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
    target.question_strict_ratio_map = {}


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
    allows_joint_ratio = bool(allows_reliability) if allows_reliability is not None else True
    if not reliability_mode_enabled or (strict_ratio and not allows_joint_ratio):
        return None
    raw_dimension = str(entry.dimension or "").strip()
    if not raw_dimension or raw_dimension == DIMENSION_UNGROUPED:
        return None
    return raw_dimension


# ---------------------------------------------------------------------------
# Ordinal option detection (for reliability analysis)
# ---------------------------------------------------------------------------

_NUMERIC_RE = re.compile(r"^\s*(\d+)(?:\s*(?:分|点|级|星))?\s*$")
_CHINESE_NUMBERS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_ORDINAL_GROUPS = [
    ["非常不满意", "不满意", "一般", "满意", "非常满意"],
    ["很不满意", "不满意", "一般", "满意", "很满意"],
    ["非常不同意", "不同意", "一般", "同意", "非常同意"],
    ["很不同意", "不同意", "一般", "同意", "很同意"],
    ["很差", "较差", "一般", "较好", "很好"],
    ["非常差", "差", "一般", "好", "非常好"],
    ["从不", "偶尔", "有时", "经常", "总是"],
    ["完全没有", "较少", "一般", "较多", "非常多"],
]
_ATTITUDE_NEUTRAL_TEXTS = frozenset(
    {"一般", "中立", "没意见", "无意见", "普通", "不好说", "说不清", "不确定"}
)
_ATTITUDE_EXTREME_MARKERS = ("非常", "很", "极其", "十分", "完全", "特别", "强烈")
_ATTITUDE_MILD_MARKERS = ("比较", "较", "不太", "有点", "稍微", "略", "有些")
_ATTITUDE_NEGATIVE_CORES = (
    "不同意", "不满意", "不认可", "不支持", "不愿意", "不赞成",
    "不太同意", "不太满意", "不太认可", "不太支持", "不太愿意", "不太赞成",
    "不太好", "反对", "不好", "不佳", "差", "没有", "少", "较少", "很少", "从不",
)
_ATTITUDE_POSITIVE_CORES = (
    "同意", "满意", "认可", "支持", "愿意", "赞成", "好", "多", "经常", "总是",
)


def _normalize_ordinal_text(value: object) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", "", text)


def _parse_numeric_ordinal(texts: list[str]) -> list[int] | None:
    values: list[int] = []
    for text in texts:
        match = _NUMERIC_RE.match(text)
        if not match:
            return None
        values.append(int(match.group(1)))
    if len(values) < 2:
        return None
    if values == list(range(values[0], values[0] + len(values))):
        return [v - min(values) for v in values]
    if values == list(range(values[0], values[0] - len(values), -1)):
        max_value = max(values)
        return [max_value - v for v in values]
    return None


def _parse_chinese_numeric_ordinal(texts: list[str]) -> list[int] | None:
    values: list[int] = []
    for text in texts:
        value = text.removesuffix("分").removesuffix("点").removesuffix("级").removesuffix("星")
        if value not in _CHINESE_NUMBERS:
            return None
        values.append(_CHINESE_NUMBERS[value])
    if len(values) < 2:
        return None
    if values == list(range(values[0], values[0] + len(values))):
        return [v - min(values) for v in values]
    if values == list(range(values[0], values[0] - len(values), -1)):
        max_value = max(values)
        return [max_value - v for v in values]
    return None


def _match_text_group(texts: list[str]) -> list[int] | None:
    if len(texts) < 2:
        return None
    for group in _ORDINAL_GROUPS:
        normalized_group = [_normalize_ordinal_text(item) for item in group]
        if texts == normalized_group[: len(texts)]:
            return list(range(len(texts)))
        if texts == list(reversed(normalized_group[-len(texts) :])):
            return list(reversed(range(len(texts))))
        if len(texts) == len(normalized_group) and texts == list(reversed(normalized_group)):
            return list(reversed(range(len(texts))))
    return None


def _score_attitude_option(text: str) -> int | None:
    if text in _ATTITUDE_NEUTRAL_TEXTS:
        return 2
    is_negative = any(core in text for core in _ATTITUDE_NEGATIVE_CORES)
    is_positive = (not is_negative) and any(core in text for core in _ATTITUDE_POSITIVE_CORES)
    if not is_negative and not is_positive:
        return None
    is_extreme = any(marker in text for marker in _ATTITUDE_EXTREME_MARKERS)
    is_mild = any(marker in text for marker in _ATTITUDE_MILD_MARKERS)
    if is_negative:
        return 0 if is_extreme and not is_mild else 1
    return 4 if is_extreme and not is_mild else 3


def _match_attitude_scale(texts: list[str]) -> list[int] | None:
    if len(texts) != 5:
        return None
    scores: list[int] = []
    for text in texts:
        score = _score_attitude_option(text)
        if score is None:
            return None
        scores.append(score)
    if sorted(scores) != list(range(5)):
        return None
    return scores


def _is_ordinal_options(option_texts: list[str]) -> bool:
    texts = [_normalize_ordinal_text(item) for item in option_texts if str(item or "").strip()]
    if len(texts) < 2:
        return False
    scores = (
        _parse_numeric_ordinal(texts)
        or _parse_chinese_numeric_ordinal(texts)
        or _match_text_group(texts)
        or _match_attitude_scale(texts)
    )
    if scores is None:
        return False
    return len(scores) == len(texts) and sorted(scores) == list(range(len(texts)))


# ---------------------------------------------------------------------------
# Per-question-type handlers
# ---------------------------------------------------------------------------


def _handle_single(
    entry: QuestionEntry,
    question_num: int,
    probs: object,
    strict_ratio: bool,
    target: "ExecutionConfig",
    idx: int,
    reliability_mode_enabled: bool,
    reliability_candidates: list[tuple[int, bool, str]],
) -> int:
    assert isinstance(entry, ChoiceQuestionEntry)
    _raise_if_all_zero_single_like(probs, question_num, "single")
    mapped_value = ("single", idx)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    raw_meta = (
        getattr(target, "questions_metadata", {}).get(question_num)
        if hasattr(target, "questions_metadata")
        else None
    )
    option_texts = list(getattr(raw_meta, "option_texts", []) or [])
    is_ordinal_single = _is_ordinal_options(option_texts) and len(option_texts) == max(
        1, entry.option_count
    )
    if is_ordinal_single:
        target.question_dimension_map[question_num] = _resolve_runtime_dimension(
            entry,
            reliability_mode_enabled=reliability_mode_enabled,
            strict_ratio=strict_ratio,
            allows_reliability=True,
        )
        reliability_candidates.append((question_num, strict_ratio, entry.question_type))
    idx += 1
    target.single_prob.append(_normalize_single_like_prob_config(cast(Any, probs), entry.option_count))
    target.single_option_fill_texts.append(
        _normalize_option_fill_texts(entry.option_fill_texts, entry.option_count)
    )
    target.single_attached_option_selects.append(copy.deepcopy(entry.attached_option_selects or []))
    return idx


def _handle_dropdown(
    entry: QuestionEntry,
    question_num: int,
    probs: object,
    strict_ratio: bool,
    target: "ExecutionConfig",
    idx: int,
    reliability_mode_enabled: bool,
    reliability_candidates: list[tuple[int, bool, str]],
) -> int:
    assert isinstance(entry, ChoiceQuestionEntry)
    _raise_if_all_zero_single_like(probs, question_num, "dropdown")
    mapped_value = ("dropdown", idx)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    target.question_dimension_map[question_num] = _resolve_runtime_dimension(
        entry,
        reliability_mode_enabled=reliability_mode_enabled,
        strict_ratio=strict_ratio,
    )
    reliability_candidates.append((question_num, strict_ratio, entry.question_type))
    idx += 1
    target.droplist_prob.append(_normalize_single_like_prob_config(cast(Any, probs), entry.option_count))
    target.droplist_option_fill_texts.append(
        _normalize_option_fill_texts(entry.option_fill_texts, entry.option_count)
    )
    return idx


def _handle_multiple(
    entry: QuestionEntry,
    question_num: int,
    probs: object,
    target: "ExecutionConfig",
    idx: int,
) -> int:
    assert isinstance(entry, ChoiceQuestionEntry)
    mapped_value = ("multiple", idx)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    idx += 1
    if not isinstance(probs, list):
        raise ValueError("多选题必须提供概率列表，数值范围0-100")
    target.multiple_prob.append([float(cast(Any, value)) for value in probs])
    target.multiple_option_fill_texts.append(
        _normalize_option_fill_texts(entry.option_fill_texts, entry.option_count)
    )
    return idx


def _normalize_matrix_row(raw_row: object, option_count: int) -> list[float] | None:
    if not isinstance(raw_row, (list, tuple)):
        return None
    cleaned: list[float] = []
    for value in raw_row:
        try:
            cleaned.append(max(0.0, float(cast(Any, value))))
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
    probs: object,
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
    reliability_candidates.append((question_num, strict_ratio, entry.question_type))
    idx += rows
    option_count = max(1, _infer_option_count(entry))

    row_weights_source: list[object] | None = None
    if isinstance(probs, list) and any(isinstance(item, (list, tuple)) for item in probs):
        row_weights_source = cast(Any, probs)
    elif isinstance(entry.custom_weights, list) and any(
        isinstance(item, (list, tuple)) for item in entry.custom_weights
    ):
        row_weights_source = cast(Any, entry.custom_weights)

    if row_weights_source is not None:
        last_row: object | None = None
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
    probs: object,
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
    reliability_candidates.append((question_num, strict_ratio, entry.question_type))
    idx += 1
    target.scale_prob.append(_normalize_single_like_prob_config(cast(Any, probs), entry.option_count))
    return idx


def _handle_slider(
    entry: QuestionEntry,
    question_num: int,
    probs: object,
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
                target_value = float(cast(Any, probs[0]))
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
    assert isinstance(entry, LocationQuestionEntry)
    mapped_value = ("location", -1)
    target.question_config_index_map[question_num] = mapped_value
    _remember_provider_mapping(target, entry, mapped_value)
    target.location_parts[question_num] = [
        str(item or "").strip() for item in list(entry.location_parts or [])[:3]
    ]


def _handle_text(
    entry: QuestionEntry,
    question_num: int,
    probs: object,
    target: "ExecutionConfig",
    idx_text: int,
) -> int:
    """Returns the new text index."""
    is_location = bool(entry.is_location)
    if not is_location:
        mapped_value = ("text", idx_text)
        target.question_config_index_map[question_num] = mapped_value
        _remember_provider_mapping(target, entry, mapped_value)
        idx_text += 1
    else:
        mapped_value = ("location", -1)
        target.question_config_index_map[question_num] = mapped_value
        _remember_provider_mapping(target, entry, mapped_value)
        target.location_parts[question_num] = [
            str(item or "").strip() for item in list(entry.location_parts or [])[:3]
        ]

    text_random_mode = (
        str(entry.text_random_mode or _TEXT_RANDOM_NONE).strip().lower()
        if isinstance(entry, TextQuestionEntry)
        else _TEXT_RANDOM_NONE
    )
    normalized_values = [str(item).strip() for item in (entry.texts or []) if str(item).strip()]
    normalized_blank_ai_flags: list[bool] = []
    normalized_blank_int_ranges: list[list[int]] = []
    if isinstance(entry, MultiTextQuestionEntry):
        raw_blank_ai_flags = entry.multi_text_blank_ai_flags or []
        if isinstance(raw_blank_ai_flags, list):
            normalized_blank_ai_flags = [bool(flag) for flag in raw_blank_ai_flags]
        raw_blank_int_ranges = entry.multi_text_blank_int_ranges or []
        if isinstance(raw_blank_int_ranges, list):
            normalized_blank_int_ranges = [
                serialize_random_int_range(item) for item in raw_blank_int_ranges
            ]
        for blank_idx, mode in enumerate(entry.multi_text_blank_modes or []):
            if str(mode or _TEXT_RANDOM_NONE).strip().lower() != _TEXT_RANDOM_INTEGER:
                continue
            target_range = (
                raw_blank_int_ranges[blank_idx] if blank_idx < len(raw_blank_int_ranges) else []
            )
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
    if entry.question_type == QuestionType.TEXT and text_random_mode in (
        _TEXT_RANDOM_NAME,
        _TEXT_RANDOM_MOBILE,
        _TEXT_RANDOM_ID_CARD,
        _TEXT_RANDOM_INTEGER,
    ):
        ai_enabled = False
        if text_random_mode == _TEXT_RANDOM_NAME:
            normalized_values = [_TEXT_RANDOM_NAME_TOKEN]
        elif text_random_mode == _TEXT_RANDOM_MOBILE:
            normalized_values = [_TEXT_RANDOM_MOBILE_TOKEN]
        elif text_random_mode == _TEXT_RANDOM_ID_CARD:
            normalized_values = [_TEXT_RANDOM_ID_CARD_TOKEN]
        else:
            text_random_range = (
                serialize_random_int_range(entry.text_random_int_range)
                if isinstance(entry, TextQuestionEntry)
                else []
            )
            if len(text_random_range) != 2:
                raise ValueError("填空题随机整数范围未设置完整")
            normalized_values = [build_random_int_token(*text_random_range)]
    if not getattr(target, "ai_answering", True):
        ai_enabled = False
        normalized_blank_ai_flags = [False] * len(normalized_blank_ai_flags)
    if not normalized_values:
        if ai_enabled:
            normalized_values = [DEFAULT_FILL_TEXT]
        else:
            raise ValueError("填空题至少需要一个候选答案")
    if isinstance(probs, list) and len(probs) == len(normalized_values):
        normalized = normalize_probabilities([float(cast(Any, value)) for value in probs])
    else:
        normalized = normalize_probabilities([1.0] * len(normalized_values))
    target.texts.append(normalized_values)
    target.texts_prob.append(normalized)
    target.text_entry_types.append(entry.question_type)
    target.text_ai_flags.append(ai_enabled)
    target.text_titles.append(str(entry.question_title or ""))
    target.multi_text_blank_modes.append(
        list(entry.multi_text_blank_modes) if isinstance(entry, MultiTextQuestionEntry) else []
    )
    target.multi_text_blank_ai_flags.append(normalized_blank_ai_flags)
    target.multi_text_blank_int_ranges.append(normalized_blank_int_ranges)
    return idx_text


# ---------------------------------------------------------------------------
# Per-question-type dispatch table
# ---------------------------------------------------------------------------


# Each handler keeps its own signature; the dispatch table wraps it so the loop
# only needs a uniform call. `idx` is a mutable holder keyed by question group;
# wrappers return ``True`` when the loop should ``continue`` (skip remaining work).
_NormalizationHandler = Callable[
    [
        QuestionEntry,
        int,
        object,
        bool,
        "ExecutionConfig",
        dict[str, int],
        bool,
        list[tuple[int, bool, str]],
    ],
    bool,
]

_NORMALIZATION_DISPATCH: dict[QuestionType, _NormalizationHandler] = {
    QuestionType.SINGLE: (
        lambda e, qn, p, sr, t, idx, rel, cand: idx.__setitem__(
            "single", _handle_single(e, qn, p, sr, t, idx["single"], rel, cand)
        )
        or False
    ),
    QuestionType.DROPDOWN: (
        lambda e, qn, p, sr, t, idx, rel, cand: idx.__setitem__(
            "dropdown", _handle_dropdown(e, qn, p, sr, t, idx["dropdown"], rel, cand)
        )
        or False
    ),
    QuestionType.MULTIPLE: (
        lambda e, qn, p, sr, t, idx, rel, cand: idx.__setitem__(
            "multiple", _handle_multiple(e, qn, p, t, idx["multiple"])
        )
        or False
    ),
    QuestionType.MATRIX: (
        lambda e, qn, p, sr, t, idx, rel, cand: idx.__setitem__(
            "matrix", _handle_matrix(e, qn, p, sr, t, idx["matrix"], rel, cand)
        )
        or False
    ),
    QuestionType.SCALE: (
        lambda e, qn, p, sr, t, idx, rel, cand: idx.__setitem__(
            "scale", _handle_scale(e, qn, p, sr, t, idx["scale"], rel, cand)
        )
        or False
    ),
    QuestionType.SCORE: (
        lambda e, qn, p, sr, t, idx, rel, cand: idx.__setitem__(
            "scale", _handle_scale(e, qn, p, sr, t, idx["scale"], rel, cand)
        )
        or False
    ),
    QuestionType.SLIDER: (
        lambda e, qn, p, sr, t, idx, rel, cand: _handle_slider(e, qn, p, t, idx["slider"])[1]
    ),
    QuestionType.ORDER: (
        lambda e, qn, p, sr, t, idx, rel, cand: _handle_order(e, qn, t) or False
    ),
    QuestionType.LOCATION: (
        lambda e, qn, p, sr, t, idx, rel, cand: _handle_location(e, qn, t) or False
    ),
    QuestionType.TEXT: (
        lambda e, qn, p, sr, t, idx, rel, cand: idx.__setitem__(
            "text", _handle_text(e, qn, p, t, idx["text"])
        )
        or False
    ),
    QuestionType.MULTI_TEXT: (
        lambda e, qn, p, sr, t, idx, rel, cand: idx.__setitem__(
            "text", _handle_text(e, qn, p, t, idx["text"])
        )
        or False
    ),
}


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

    idx: dict[str, int] = {
        "single": 0,
        "dropdown": 0,
        "multiple": 0,
        "matrix": 0,
        "scale": 0,
        "slider": 0,
        "text": 0,
    }
    reliability_candidates: list[tuple[int, bool, str]] = []

    for idx_num, entry in enumerate(entries, start=1):
        question_num = entry.question_num if entry.question_num is not None else idx_num
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

        handler = _NORMALIZATION_DISPATCH.get(QuestionType(str(entry.question_type)))
        if handler is None:
            continue
        if handler(entry, question_num, probs, strict_ratio, target, idx, reliability_mode_enabled, reliability_candidates):
            continue

    _apply_reliability_fallback(target, reliability_mode_enabled, reliability_candidates)
