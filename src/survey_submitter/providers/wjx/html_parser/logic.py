"""跳转规则与显示条件解析。

纯属性 / 正则层面的工作：``relation`` 决定显示条件，``jumpto`` 决定跳题，
外加分区标题（cut field）把显示条件向下传播。
"""

from __future__ import annotations

import re
from typing import Any, cast

from survey_submitter.providers.contracts import (
    LOGIC_PARSE_STATUS_COMPLETE,
    LOGIC_PARSE_STATUS_NONE,
    DisplayCondition,
    JumpRule,
)
from survey_submitter.providers.match_utils import normalize_match_text

from ..regexes import WJX_JUMP_TARGET_RE, WJX_RELATION_CHUNK_RE
from .features import Marker
from .texts import class_xpath

_TERMINATE_KEYWORDS = ("结束作答", "结束答题", "结束填写", "终止作答", "停止作答")
_UNCONDITIONAL_JUMP_ATTRS = (
    "jumpto",
    "data-jumpto",
    "goto",
    "data-goto",
    "anyjump",
    "data-anyjump",
)
_RELATION_SPLIT_RE = re.compile(r"\s*[|]\s*")
_OPTION_SPLIT_RE = re.compile(r"[,;]")


def _parse_jump_target(raw) -> int | None:
    text = normalize_match_text(raw)
    if not text:
        return None
    match = WJX_JUMP_TARGET_RE.fullmatch(text)
    if not match:
        return None
    try:
        return int(match.group("signed") or match.group("target") or "")
    except (ValueError, TypeError):
        return None


def _jump_target_terminates(jump_to_num: int, option_text: str | None) -> bool:
    if option_text and any(keyword in option_text for keyword in _TERMINATE_KEYWORDS):
        return True
    return int(jump_to_num or 0) in {1, -1}


def jump_rules(features, option_texts: list[str]) -> tuple[bool, list[dict[str, Any]]]:
    """解析选项级与整题级的跳题规则。"""
    rules: list[dict[str, Any]] = []
    for option_index, node in enumerate(features.selectable_nodes):
        jump_to_raw = node.get("jumpto") or node.get("data-jumpto")
        if jump_to_raw:
            jump_to_num = _parse_jump_target(jump_to_raw)
            if jump_to_num:
                option_text = (
                    option_texts[option_index] if option_index < len(option_texts) else None
                )
                rules.append(
                    {
                        "option_index": option_index,
                        "jumpto": jump_to_num,
                        "option_text": option_text,
                        "terminates_survey": _jump_target_terminates(jump_to_num, option_text),
                    }
                )

    if Marker.JUMP in features.markers:
        unconditional: int | None = None
        for attr_name in _UNCONDITIONAL_JUMP_ATTRS:
            unconditional = _parse_jump_target(features.node.get(attr_name))
            if unconditional:
                break
        if unconditional and not any(
            cast(JumpRule, rule)["option_index"] < 0
            and cast(JumpRule, rule)["jumpto"] == unconditional
            for rule in rules
        ):
            rules.append({"option_index": -1, "jumpto": unconditional, "option_text": None})
    return Marker.JUMP in features.markers or bool(rules), rules


def display_conditions(relation: str) -> tuple[bool, list[dict[str, Any]]]:
    """把 ``relation`` 字符串解析成显示条件列表。"""
    if not relation:
        return False, []
    conditions: list[dict[str, Any]] = []
    seen: set[tuple[int, tuple[int, ...]]] = set()
    for chunk in _RELATION_SPLIT_RE.split(relation):
        text = normalize_match_text(chunk)
        if not text:
            continue
        match = WJX_RELATION_CHUNK_RE.fullmatch(text)
        if not match:
            continue
        try:
            source_question_num = int(match.group("source"))
        except (ValueError, TypeError):
            continue
        option_indices: list[int] = []
        seen_indices: set[int] = set()
        for raw_option in _OPTION_SPLIT_RE.split(str(match.group("options") or "")):
            try:
                option_num = int(str(raw_option or "").strip())
            except (ValueError, TypeError):
                continue
            if option_num <= 0:
                continue
            option_index = option_num - 1
            if option_index in seen_indices:
                continue
            seen_indices.add(option_index)
            option_indices.append(option_index)
        if source_question_num <= 0 or not option_indices:
            continue
        dedupe_key = (source_question_num, tuple(option_indices))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        conditions.append(
            {
                "condition_question_num": source_question_num,
                "condition_mode": "selected",
                "condition_option_indices": option_indices,
                "raw_relation": text,
            }
        )
    return bool(conditions), conditions


def attach_display_condition_metadata(questions_info: list[dict[str, Any]]) -> None:
    """把“谁控制了我的显示”反向登记到源题目上。"""
    by_num: dict[int, dict[str, Any]] = {}
    for info in questions_info:
        try:
            question_num = int(info.get("num") or 0)
        except (ValueError, TypeError):
            question_num = 0
        if question_num > 0 and question_num not in by_num:
            by_num[question_num] = info

    for info in questions_info:
        conditions = info.get("display_conditions")
        if not isinstance(conditions, list) or not conditions:
            continue
        try:
            target_question_num = int(info.get("num") or 0)
        except (ValueError, TypeError):
            target_question_num = 0
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            typed_condition = cast(DisplayCondition, condition)
            try:
                source_question_num = int(typed_condition["condition_question_num"] or 0)
            except (ValueError, TypeError):
                source_question_num = 0
            option_indices = typed_condition.get("condition_option_indices") or []
            if source_question_num <= 0 or not isinstance(option_indices, list):
                continue
            source_info = by_num.get(source_question_num)
            if not source_info:
                continue
            targets = source_info.setdefault("controls_display_targets", [])
            if not isinstance(targets, list):
                targets = []
                source_info["controls_display_targets"] = targets
            normalized_indices: list[int] = []
            seen_indices: set[int] = set()
            for raw_index in option_indices:
                try:
                    index = int(raw_index)
                except (ValueError, TypeError):
                    continue
                if index < 0 or index in seen_indices:
                    continue
                seen_indices.add(index)
                normalized_indices.append(index)
            if not normalized_indices:
                continue
            duplicate = any(
                isinstance(existing, dict)
                and int(cast(DisplayCondition, existing)["target_question_num"] or 0)
                == target_question_num
                and list(cast(DisplayCondition, existing).get("condition_option_indices") or [])
                == normalized_indices
                for existing in targets
            )
            if duplicate:
                continue
            targets.append(
                {
                    "target_question_num": target_question_num,
                    "condition_option_indices": normalized_indices,
                    "condition_mode": str(
                        typed_condition.get("condition_mode") or "selected"
                    ).strip()
                    or "selected",
                }
            )

    for info in questions_info:
        targets = info.get("controls_display_targets")
        if isinstance(targets, list) and targets:
            targets.sort(
                key=lambda item: (
                    int(cast(DisplayCondition, item)["target_question_num"] or 0)
                    if isinstance(item, dict)
                    else 0,
                    tuple(cast(DisplayCondition, item).get("condition_option_indices") or [])
                    if isinstance(item, dict)
                    else (),
                )
            )
            info["has_dependent_display_logic"] = True
        else:
            info["controls_display_targets"] = []
            info["has_dependent_display_logic"] = False


def finalize_logic_parse_status(questions_info: list[dict[str, Any]]) -> None:
    attach_display_condition_metadata(questions_info)
    for question in questions_info:
        logic_present = (
            bool(question.get("has_jump"))
            or bool(question.get("has_display_condition"))
            or bool(question.get("has_dependent_display_logic"))
        )
        question["logic_parse_status"] = (
            LOGIC_PARSE_STATUS_COMPLETE if logic_present else LOGIC_PARSE_STATUS_NONE
        )


_XP_CUT_FIELDS = f".//div[{class_xpath('cutfield')}][@qtopic]"


def propagate_cut_field(container, questions_info: list[dict[str, Any]]) -> None:
    """分区标题（cut field）上的 relation 覆盖本分区内所有题目。"""
    cut_fields = container.xpath(_XP_CUT_FIELDS)
    if not cut_fields:
        return
    starts: list[int] = []
    relations: dict[int, str] = {}
    for cut in cut_fields:
        raw_topic = cut.get("qtopic")
        try:
            start = int(str(raw_topic))
        except (TypeError, ValueError):
            continue
        starts.append(start)
        relation = str(cut.get("relation") or "").strip()
        if relation:
            relations[start] = relation
    if not starts:
        return
    ordered = sorted(set(starts))
    numbered = [int(q["num"]) for q in questions_info if str(q.get("num", "")).isdigit()]
    if not numbered:
        return
    max_topic = max(numbered)

    for index, start in enumerate(ordered):
        relation = relations.get(start)
        if not relation:
            continue
        end = (ordered[index + 1] - 1) if index + 1 < len(ordered) else max_topic
        condition_hit, conditions = display_conditions(relation)
        if not condition_hit:
            continue
        for question in questions_info:
            num = question.get("num")
            if not isinstance(num, int) or not (start <= num <= end):
                continue
            if question.get("has_display_condition"):
                continue
            question["has_display_condition"] = True
            question["display_conditions"] = conditions


__all__ = [
    "attach_display_condition_metadata",
    "display_conditions",
    "finalize_logic_parse_status",
    "jump_rules",
    "propagate_cut_field",
]
