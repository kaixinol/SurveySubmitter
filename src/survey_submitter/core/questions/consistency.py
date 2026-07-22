from __future__ import annotations

import threading
from loguru import logger
from typing import Any, Sequence

from pydantic import BaseModel, Field, field_validator

from survey_submitter.core.config.schema import AnswerRulesConfig
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


class AnswerRule(BaseModel):
    id: str = ""
    condition_question_num: int = Field(ge=1)
    condition_mode: str
    condition_option_indices: list[int]
    target_question_num: int = Field(ge=1)
    action_mode: str
    target_option_indices: list[int]
    condition_row_index: int | None = None
    target_row_index: int | None = None

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, v: Any) -> str:
        return str(v or "").strip()

    @field_validator("condition_question_num", "target_question_num", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> int:
        try:
            return int(v)
        except (ValueError, TypeError):
            return -1

    @field_validator("condition_mode", "action_mode", mode="before")
    @classmethod
    def normalize_mode(cls, v: Any) -> str:
        return str(v or "").strip()

    @field_validator("condition_option_indices", "target_option_indices", mode="before")
    @classmethod
    def coerce_int_list(cls, v: Any) -> list[int]:
        if not isinstance(v, list):
            return []
        result: list[int] = []
        seen: set[int] = set()
        for item in v:
            try:
                idx = int(item)
                if idx >= 0 and idx not in seen:
                    seen.add(idx)
                    result.append(idx)
            except (ValueError, TypeError):
                continue
        return sorted(result)

    @field_validator("condition_row_index", "target_row_index", mode="before")
    @classmethod
    def coerce_optional_int(cls, v: Any) -> int | None:
        if v is None:
            return None
        try:
            val = int(v)
            return val if val >= 0 else None
        except (ValueError, TypeError):
            return None


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
        try:
            q_num = int(question.num)
        except (ValueError, TypeError):
            continue
        if q_num <= 0:
            continue
        question_map[q_num] = question
    return question_map


def sanitize_answer_rules(
    answer_rules: AnswerRulesConfig | Sequence[dict[str, object]] | None,
    questions_info: Sequence[SurveyQuestionMeta | dict[str, object]] | None = None,
) -> tuple[AnswerRulesConfig, dict[str, int]]:

    stats = {"invalid": 0, "unsupported": 0}
    sanitized_constraints: list[dict[str, object]] = []
    question_map = _build_question_info_map(questions_info)
    has_question_info = bool(question_map)

    if isinstance(answer_rules, AnswerRulesConfig):
        constraint_rules = answer_rules.constraints or []
        per_question_rules = list(answer_rules.per_question or [])
    elif answer_rules:
        constraint_rules = list(answer_rules)
        per_question_rules = []
    else:
        constraint_rules = []
        per_question_rules = []

    for item in constraint_rules:
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
        sanitized_constraints.append(normalized)
    return AnswerRulesConfig(constraints=sanitized_constraints, per_question=per_question_rules), stats


def normalize_rule_dict(raw: dict[str, object]) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    try:
        rule = AnswerRule.model_validate(raw)
    except Exception:
        return None
    if rule.condition_question_num <= 0 or rule.target_question_num <= 0:
        return None
    if rule.condition_mode not in _CONDITION_MODES:
        return None
    if rule.action_mode not in _ACTION_MODES:
        return None
    if not rule.condition_option_indices or not rule.target_option_indices:
        return None
    if not rule.id:
        rule.id = (
            f"rule-{rule.condition_question_num}-{rule.target_question_num}-"
            f"{len(rule.condition_option_indices)}-{len(rule.target_option_indices)}"
        )
    return rule.model_dump(exclude_none=True)


def _normalize_rule(raw: dict[str, object]) -> AnswerRule | None:
    if not isinstance(raw, dict):
        return None
    try:
        rule = AnswerRule.model_validate(raw)
    except Exception:
        return None
    if rule.condition_question_num <= 0 or rule.target_question_num <= 0:
        return None
    if rule.condition_mode not in _CONDITION_MODES:
        return None
    if rule.action_mode not in _ACTION_MODES:
        return None
    if not rule.condition_option_indices or not rule.target_option_indices:
        return None
    if not rule.id:
        rule.id = (
            f"rule-{rule.condition_question_num}-{rule.target_question_num}-"
            f"{len(rule.condition_option_indices)}-{len(rule.target_option_indices)}"
        )
    return rule


def reset_consistency_context(
    answer_rules: AnswerRulesConfig | Sequence[dict[str, object]] | None = None,
    questions_info: Sequence[SurveyQuestionMeta | dict[str, object]] | None = None,
) -> None:

    parsed_rules: list[AnswerRule] = []
    sanitized_result, _ = sanitize_answer_rules(answer_rules, questions_info)
    for item in sanitized_result.constraints:
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

    def _extract_indices(values: Any) -> list[int]:
        if not isinstance(values, list):
            return []
        result: list[int] = []
        for item in values:
            try:
                idx = int(item)
                if idx >= 0:
                    result.append(idx)
            except (ValueError, TypeError):
                continue
        return result

    if rule.condition_row_index is not None:
        row_answers = record.row_answers if hasattr(record, "row_answers") else {}
        selected_indices = set(_extract_indices(row_answers.get(rule.condition_row_index, [])))
    else:
        selected_indices = set(_extract_indices(getattr(record, "selected_indices", [])))
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
        logger.warning(
            f"条件规则[{rule.id}]命中但目标选项越界，已忽略该规则（题号={rule.target_question_num}）"
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
        logger.warning(
            f"条件规则[{rule.id}]命中后无可用选项，已回退原概率（题号={rule.target_question_num}）"
        )
        return list(base_probabilities)
    logger.info(
        f"条件规则[{rule.id}]已生效：条件题={rule.condition_question_num}，目标题={rule.target_question_num}，动作={rule.action_mode}，目标选项={sorted(valid_indices)}"
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
        logger.warning(
            f"条件规则[{rule.id}]命中但目标选项越界，已忽略该规则（题号={rule.target_question_num}）"
        )
        return set(), set(), rule.id
    logger.info(
        f"条件规则[{rule.id}]已生效：条件题={rule.condition_question_num}，目标题={rule.target_question_num}，动作={rule.action_mode}，目标选项={sorted(valid_indices)}"
    )
    if rule.action_mode == "must_select":
        return set(valid_indices), set(), rule.id
    return set(), set(valid_indices), rule.id
