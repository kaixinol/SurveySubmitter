from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from survey_submitter.core.questions.types import QuestionType, TypeCode
from survey_submitter.providers.contracts import (
    MatrixQuestionMeta,
    MultipleChoiceQuestionMeta,
    RatingQuestionMeta,
    SingleChoiceQuestionMeta,
    SliderQuestionMeta,
    SurveyQuestionMeta,
    TextQuestionMeta,
    ensure_survey_question_meta,
)

QuestionMetaLike = SurveyQuestionMeta | Mapping[str, Any]

__all__ = [
    "QuestionMetaLike",
    "count_positive_weights",
    "find_all_zero_attached_selects",
    "find_all_zero_matrix_rows",
    "infer_question_entry_type",
    "normalize_attached_option_selects",
    "normalize_fillable_option_indices",
]


def count_positive_weights(raw_weights: Any) -> int:
    if not isinstance(raw_weights, (list, tuple)):
        return 0
    count = 0
    for value in raw_weights:
        try:
            if float(value) > 0:
                count += 1
        except Exception:
            continue
    return count


def find_all_zero_matrix_rows(raw_weights: Any) -> List[int]:
    if not isinstance(raw_weights, list) or not raw_weights:
        return []
    if any(isinstance(item, (list, tuple)) for item in raw_weights):
        invalid_rows: List[int] = []
        for row_index, row_weights in enumerate(raw_weights, start=1):
            if isinstance(row_weights, (list, tuple)) and row_weights and count_positive_weights(row_weights) <= 0:
                invalid_rows.append(row_index)
        return invalid_rows
    return [0] if count_positive_weights(raw_weights) <= 0 else []


def find_all_zero_attached_selects(attached_configs: Any) -> List[Tuple[int, str]]:
    issues: List[Tuple[int, str]] = []
    for cfg_idx, cfg in enumerate(list(attached_configs or []), start=1):
        if not isinstance(cfg, dict):
            continue
        weights = cfg.get("weights")
        if isinstance(weights, list) and weights and count_positive_weights(weights) <= 0:
            issues.append((cfg_idx, str(cfg.get("option_text") or "").strip()))
    return issues


def infer_question_entry_type(question: QuestionMetaLike) -> str:
    meta = ensure_survey_question_meta(question)

    if meta.type_code == TypeCode.DESCRIPTION:
        return QuestionType.DESCRIPTION
    if isinstance(meta, SliderQuestionMeta):
        return QuestionType.SLIDER
    if isinstance(meta, TextQuestionMeta):
        if meta.is_location or meta.type_code == TypeCode.LOCATION:
            return QuestionType.LOCATION
        if meta.text_inputs > 1:
            return QuestionType.MULTI_TEXT
        return QuestionType.TEXT
    if isinstance(meta, MultipleChoiceQuestionMeta):
        return QuestionType.MULTIPLE
    if isinstance(meta, SingleChoiceQuestionMeta):
        match meta.type_code:
            case TypeCode.DROPDOWN:
                return QuestionType.DROPDOWN
            case TypeCode.ORDER:
                return QuestionType.ORDER
            case _:
                return QuestionType.SINGLE
    if isinstance(meta, MatrixQuestionMeta):
        return QuestionType.MATRIX
    if isinstance(meta, RatingQuestionMeta):
        match meta.type_code:
            case TypeCode.SCORE:
                return QuestionType.SCORE
            case _:
                return QuestionType.SCALE

    match meta.type_code:
        case TypeCode.SINGLE:
            return QuestionType.SINGLE
        case TypeCode.MULTIPLE:
            return QuestionType.MULTIPLE
        case TypeCode.MATRIX:
            return QuestionType.MATRIX
        case TypeCode.DROPDOWN:
            return QuestionType.DROPDOWN
        case TypeCode.SLIDER:
            return QuestionType.SLIDER
        case TypeCode.ORDER:
            return QuestionType.ORDER
        case _:
            return QuestionType.SINGLE


def normalize_attached_option_selects(
    parsed_configs: Any,
    existing_configs: Any = None,
) -> List[Dict[str, Any]]:
    parsed_list = parsed_configs if isinstance(parsed_configs, list) else []
    existing_map: Dict[int, Dict[str, Any]] = {}
    if isinstance(existing_configs, list):
        for item in existing_configs:
            if not isinstance(item, dict):
                continue
            raw_option_index = item.get("option_index")
            if raw_option_index is None:
                continue
            try:
                option_index = int(raw_option_index)
            except Exception:
                continue
            existing_map[option_index] = item
    normalized: List[Dict[str, Any]] = []
    for item in parsed_list:
        if not isinstance(item, dict):
            continue
        raw_option_index = item.get("option_index")
        if raw_option_index is None:
            continue
        try:
            option_index = int(raw_option_index)
        except Exception:
            continue
        option_text = str(item.get("option_text") or "").strip()
        select_options_raw = item.get("select_options")
        if not isinstance(select_options_raw, list):
            continue
        select_options = [str(opt or "").strip() for opt in select_options_raw if str(opt or "").strip()]
        if not select_options:
            continue
        weights = None
        existing_item = existing_map.get(option_index)
        if existing_item is not None:
            existing_weights = existing_item.get("weights")
            if isinstance(existing_weights, list) and existing_weights:
                weights = []
                for idx in range(len(select_options)):
                    raw_weight = existing_weights[idx] if idx < len(existing_weights) else 0.0
                    try:
                        weights.append(max(0.0, float(raw_weight)))
                    except Exception:
                        weights.append(0.0)
                if not any(weight > 0 for weight in weights):
                    weights = None
        normalized.append(
            {
                "option_index": option_index,
                "option_text": option_text,
                "select_options": select_options,
                "weights": weights,
            }
        )
    return normalized


def normalize_fillable_option_indices(
    parsed_indices: Any,
    option_count: int,
    existing_indices: Any = None,
) -> List[int]:
    source = parsed_indices if isinstance(parsed_indices, list) else existing_indices
    if not isinstance(source, list):
        return []
    total = max(0, int(option_count or 0))
    normalized: List[int] = []
    seen: set[int] = set()
    for raw in source:
        try:
            index = int(raw)
        except Exception:
            continue
        if index < 0 or index >= total or index in seen:
            continue
        seen.add(index)
        normalized.append(index)
    return normalized
