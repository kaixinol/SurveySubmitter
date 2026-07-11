from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Sequence, cast

from survey_submitter.core.persona.context import get_answered
from survey_submitter.core.questions.types import TypeCode
from survey_submitter.providers.contracts import SurveyQuestionMeta, ensure_survey_question_meta

_thread_local = threading.local()
_CONDITION_MODES = {"selected", "not_selected"}
_ACTION_MODES = {"must_select", "must_not_select"}
_SUPPORTED_RULE_TYPE_CODES = {
    TypeCode.SINGLE,
    TypeCode.MULTIPLE,
    TypeCode.MATRIX,
    TypeCode.SCORE,
    TypeCode.SCALE,
}


@dataclass
class AnswerRule:
    id: str
    condition_question_num: int
    condition_mode: str
    condition_option_indices: list[int]
    target_question_num: int
    action_mode: str
    target_option_indices: list[int]
    condition_row_index: int | None = None
    target_row_index: int | None = None


def _to_int(value: str | int | float | None, default: int = 0) -> int:
    try:
        return int(cast(Any, value))
    except (ValueError, TypeError):
        return int(default)


def _to_int_list(values: list[str | int | float] | None) -> list[int]:
    if not isinstance(values, list):
        return []
    result: list[int] = []
    seen = set()
    for item in values:
        idx = _to_int(item, -1)
        if idx < 0 or idx in seen:
            continue
        seen.add(idx)
        result.append(idx)
    return sorted(result)


def _normalize_question_type_code(value: str | None) -> str:
    return str(value or "").strip()


def question_supports_answer_rule(question: dict[str, object] | SurveyQuestionMeta) -> bool:
    if not isinstance(question, (dict, SurveyQuestionMeta)):
        return False
    question_meta = ensure_survey_question_meta(question)
    type_code = _normalize_question_type_code(question_meta.type_code)
    return type_code in _SUPPORTED_RULE_TYPE_CODES


def _build_question_info_map(
    questions_info: Sequence[SurveyQuestionMeta | dict[str, object]] | None,
) -> dict[int, SurveyQuestionMeta]:
    question_map: dict[int, SurveyQuestionMeta] = {}
    for item in questions_info or []:
        if not isinstance(item, (dict, SurveyQuestionMeta)):
            continue
        question = ensure_survey_question_meta(item)
        q_num = _to_int(question.num, 0)
        if q_num <= 0:
            continue
        question_map[q_num] = question
    return question_map


def sanitize_answer_rules(
    answer_rules: Sequence[dict[str, object]] | None,
    questions_info: Sequence[SurveyQuestionMeta | dict[str, object]] | None = None,
) -> tuple[list[dict[str, object]], dict[str, int]]:

    stats = {"invalid": 0, "unsupported": 0}
    sanitized: list[dict[str, object]] = []
    question_map = _build_question_info_map(questions_info)
    has_question_info = bool(question_map)

    for item in answer_rules or []:
        normalized = normalize_rule_dict(item)
        if not normalized:
            stats["invalid"] += 1
            continue
        if has_question_info:
            condition_info = question_map.get(normalized["condition_question_num"])
            target_info = question_map.get(normalized["target_question_num"])
            if not condition_info or not target_info:
                stats["unsupported"] += 1
                continue
            if not question_supports_answer_rule(
                condition_info
            ) or not question_supports_answer_rule(target_info):
                stats["unsupported"] += 1
                continue
        sanitized.append(normalized)
    return sanitized, stats


def normalize_rule_dict(raw: dict[str, object]) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    condition_question_num = _to_int(cast(Any, raw.get("condition_question_num")), -1)
    target_question_num = _to_int(cast(Any, raw.get("target_question_num")), -1)
    condition_mode = str(raw.get("condition_mode") or "").strip()
    action_mode = str(raw.get("action_mode") or "").strip()
    if condition_question_num <= 0 or target_question_num <= 0:
        return None
    if condition_mode not in _CONDITION_MODES:
        return None
    if action_mode not in _ACTION_MODES:
        return None
    condition_option_indices = _to_int_list(cast(Any, raw.get("condition_option_indices")))
    target_option_indices = _to_int_list(cast(Any, raw.get("target_option_indices")))
    if not condition_option_indices or not target_option_indices:
        return None

    condition_row_index: int | None = None
    target_row_index: int | None = None
    raw_cri = raw.get("condition_row_index")
    if raw_cri is not None:
        cri = _to_int(cast(Any, raw_cri), -1)
        if cri >= 0:
            condition_row_index = cri
    raw_tri = raw.get("target_row_index")
    if raw_tri is not None:
        tri = _to_int(cast(Any, raw_tri), -1)
        if tri >= 0:
            target_row_index = tri
    rule_id = str(raw.get("id") or "").strip() or (
        f"rule-{condition_question_num}-{target_question_num}-{len(condition_option_indices)}-{len(target_option_indices)}"
    )
    result: dict[str, object] = {
        "id": rule_id,
        "condition_question_num": condition_question_num,
        "condition_mode": condition_mode,
        "condition_option_indices": condition_option_indices,
        "target_question_num": target_question_num,
        "action_mode": action_mode,
        "target_option_indices": target_option_indices,
    }
    if condition_row_index is not None:
        result["condition_row_index"] = condition_row_index
    if target_row_index is not None:
        result["target_row_index"] = target_row_index
    return result


def _normalize_rule(raw: dict[str, object]) -> AnswerRule | None:
    normalized = normalize_rule_dict(raw)
    if not normalized:
        return None
    return AnswerRule(
        id=cast(str, normalized["id"]),
        condition_question_num=cast(int, normalized["condition_question_num"]),
        condition_mode=cast(str, normalized["condition_mode"]),
        condition_option_indices=cast(list[int], normalized["condition_option_indices"]),
        target_question_num=cast(int, normalized["target_question_num"]),
        action_mode=cast(str, normalized["action_mode"]),
        target_option_indices=cast(list[int], normalized["target_option_indices"]),
        condition_row_index=cast(int | None, normalized.get("condition_row_index")),
        target_row_index=cast(int | None, normalized.get("target_row_index")),
    )


def reset_consistency_context(
    answer_rules: Sequence[dict[str, object]] | None = None,
    questions_info: Sequence[SurveyQuestionMeta | dict[str, object]] | None = None,
) -> None:

    parsed_rules: list[AnswerRule] = []
    sanitized_rules, _ = sanitize_answer_rules(answer_rules, questions_info)
    for item in sanitized_rules:
        normalized = _normalize_rule(item)
        if normalized:
            parsed_rules.append(normalized)
    _thread_local.answer_rules = parsed_rules


def _get_answer_rules() -> list[AnswerRule]:
    rules = getattr(_thread_local, "answer_rules", None)
    if not rules:
        return []
    return list(rules)


def _sanitize_probabilities(probabilities: Sequence[float]) -> list[float]:
    result: list[float] = []
    for value in probabilities:
        try:
            weight = float(value)
        except (ValueError, TypeError):
            weight = 0.0
        if weight < 0:
            weight = 0.0
        result.append(weight)
    return result


def _is_rule_triggered(rule: AnswerRule) -> bool:
    if rule.condition_question_num >= rule.target_question_num:
        return False
    answered = get_answered()
    if not answered:
        return False
    record = answered.get(rule.condition_question_num)
    if record is None:
        return False

    if rule.condition_row_index is not None:
        selected_indices = set(_to_int_list(cast(Any, record.row_answers.get(rule.condition_row_index, []))))
    else:
        selected_indices = set(_to_int_list(getattr(record, "selected_indices", [])))
    condition_set = set(rule.condition_option_indices)
    if not condition_set:
        return False
    if rule.condition_mode == "selected":
        return bool(selected_indices.intersection(condition_set))
    if rule.condition_mode == "not_selected":
        return bool(selected_indices.isdisjoint(condition_set))
    return False


def _pick_latest_triggered_rule(
    question_number: int, row_index: int | None = None
) -> AnswerRule | None:
    selected_rule: AnswerRule | None = None
    for rule in _get_answer_rules():
        if rule.target_question_num != question_number:
            continue
        if rule.target_row_index != row_index:
            continue
        if _is_rule_triggered(rule):
            selected_rule = rule
    return selected_rule


def _resolve_valid_rule_indices(rule: AnswerRule, option_count: int) -> set[int]:
    return {idx for idx in rule.target_option_indices if 0 <= idx < option_count}


def _apply_rule(
    base_probabilities: list[float],
    rule: AnswerRule,
) -> list[float]:
    if not base_probabilities:
        return []
    valid_indices = _resolve_valid_rule_indices(rule, len(base_probabilities))
    if not valid_indices:
        logging.warning(
            "条件规则[%s]命中但目标选项越界，已忽略该规则（题号=%s）",
            rule.id,
            rule.target_question_num,
        )
        return list(base_probabilities)
    if rule.action_mode == "must_select":
        adjusted = [
            weight if idx in valid_indices else 0.0 for idx, weight in enumerate(base_probabilities)
        ]
    else:
        adjusted = [
            0.0 if idx in valid_indices else weight for idx, weight in enumerate(base_probabilities)
        ]
    if sum(adjusted) <= 0:
        logging.warning(
            "条件规则[%s]命中后无可用选项，已回退原概率（题号=%s）",
            rule.id,
            rule.target_question_num,
        )
        return list(base_probabilities)
    logging.info(
        "条件规则[%s]已生效：条件题=%s，目标题=%s，动作=%s，目标选项=%s",
        rule.id,
        rule.condition_question_num,
        rule.target_question_num,
        rule.action_mode,
        sorted(valid_indices),
    )
    return adjusted


def apply_single_like_consistency(
    probabilities: Sequence[float],
    question_number: int,
) -> list[float]:

    base_probabilities = _sanitize_probabilities(probabilities)
    rule = _pick_latest_triggered_rule(question_number, row_index=None)
    if rule is None:
        return base_probabilities
    return _apply_rule(base_probabilities, rule)


def apply_matrix_row_consistency(
    probabilities: Sequence[float],
    question_number: int,
    row_index: int,
) -> list[float]:

    base_probabilities = _sanitize_probabilities(probabilities)
    rule = _pick_latest_triggered_rule(question_number, row_index=row_index)
    if rule is None:
        return base_probabilities
    return _apply_rule(base_probabilities, rule)


def get_multiple_rule_constraint(
    question_number: int,
    option_count: int,
) -> tuple[set[int], set[int], str | None]:

    rule = _pick_latest_triggered_rule(question_number, row_index=None)
    if rule is None:
        return set(), set(), None
    valid_indices = _resolve_valid_rule_indices(rule, option_count)
    if not valid_indices:
        logging.warning(
            "条件规则[%s]命中但目标选项越界，已忽略该规则（题号=%s）",
            rule.id,
            rule.target_question_num,
        )
        return set(), set(), rule.id
    logging.info(
        "条件规则[%s]已生效：条件题=%s，目标题=%s，动作=%s，目标选项=%s",
        rule.id,
        rule.condition_question_num,
        rule.target_question_num,
        rule.action_mode,
        sorted(valid_indices),
    )
    if rule.action_mode == "must_select":
        return set(valid_indices), set(), rule.id
    return set(), set(valid_indices), rule.id
