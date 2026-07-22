from __future__ import annotations

import re
from typing import Any

from survey_submitter.core.config.schema import QuestionInfo
from survey_submitter.core.questions.meta_helpers import (
    count_positive_weights,
    find_all_zero_attached_selects,
    find_all_zero_matrix_rows,
)
from survey_submitter.core.questions.schema import (
    ChoiceQuestionAnswerConfig,
    MultiTextQuestionAnswerConfig,
    TextQuestionAnswerConfig,
)
from survey_submitter.core.questions.types import QuestionType, CHOICE_TYPES, TEXT_TYPES
from survey_submitter.providers.contracts import SurveyQuestionMeta, ensure_survey_question_meta

__all__ = ["validate_question_config"]

MAX_DISPLAYED_UNSUPPORTED_ITEMS = 12


_TEXT_MIN_LENGTH_PATTERNS = (
    re.compile(r"(?:至少|最少|不少于|不低于)\s*(\d+)\s*(?:个)?(?:字|字符|汉字)"),
    re.compile(r"(\d+)\s*(?:个)?(?:字|字符|汉字)\s*(?:以上|起)"),
)


def _extract_text_min_length(*fragments: Any) -> int | None:

    limits: list[int] = []
    for fragment in fragments:
        text = str(fragment or "").strip()
        if not text:
            continue
        for pattern in _TEXT_MIN_LENGTH_PATTERNS:
            for match in pattern.finditer(text):
                limits.append(int(match.group(1)))
    return max(limits) if limits else None


def _is_text_ai_enabled(qi: QuestionInfo) -> bool:
    question_type = str(qi.question_type or "").strip()
    if question_type == QuestionType.TEXT:
        return bool(qi.details.answer_config.ai_enabled)
    if question_type == QuestionType.MULTI_TEXT and isinstance(
        qi.details.answer_config, MultiTextQuestionAnswerConfig
    ):
        blank_flags = qi.details.answer_config.multi_text_blank_ai_flags or []
        return bool(qi.details.answer_config.ai_enabled) or (
            bool(blank_flags) and all(bool(flag) for flag in blank_flags)
        )
    return False


def _text_random_mode_label(raw_mode: Any) -> str:
    mode = str(raw_mode or "").strip().lower()
    return {
        "name": "随机姓名",
        "mobile": "随机手机号",
        "id_card": "随机身份证号",
        "integer": "随机整数",
    }.get(mode, "随机处理")


def _display_question_num(raw_num: Any, question_info: SurveyQuestionMeta | None) -> Any:
    if question_info is not None:
        display_num = getattr(question_info, "display_num", None)
        if display_num not in (None, ""):
            return display_num
    return raw_num


def _pick_config_weights(qi: QuestionInfo) -> Any:
    distribution_mode = str(qi.details.distribution_mode or "").strip().lower()
    custom_weights = qi.details.custom_weights
    probabilities = qi.details.probabilities
    return (
        custom_weights
        if distribution_mode == "custom" and custom_weights not in (None, [])
        else probabilities
    )


def _build_question_info_map(
    questions_info: list[SurveyQuestionMeta | dict[str, Any]] | None,
) -> tuple[dict[int, SurveyQuestionMeta], list[SurveyQuestionMeta]]:
    question_info_map: dict[int, SurveyQuestionMeta] = {}
    unsupported_questions: list[SurveyQuestionMeta] = []
    for item in questions_info or []:
        if not isinstance(item, (dict, SurveyQuestionMeta)):
            continue
        meta = ensure_survey_question_meta(item)
        if meta.num > 0:
            question_info_map[meta.num] = meta
        if bool(meta.unsupported):
            unsupported_questions.append(meta)
    return question_info_map, unsupported_questions


def _format_unsupported_error(unsupported_questions: list[SurveyQuestionMeta]) -> str:
    lines = ["当前问卷包含暂不支持的题型，已禁止启动："]
    for item in unsupported_questions[:MAX_DISPLAYED_UNSUPPORTED_ITEMS]:
        title = str(item.title or f"第{item.num}题").strip()
        provider_type = str(item.provider_type or item.type_code or "未知类型").strip()
        reason = str(item.unsupported_reason or "").strip()
        suffix = f"（{provider_type}，{reason}）" if reason else f"（{provider_type}）"
        lines.append(f"  - 第 {item.num} 题：{title}{suffix}")
    if len(unsupported_questions) > MAX_DISPLAYED_UNSUPPORTED_ITEMS:
        lines.append(
            f"  - 其余 {len(unsupported_questions) - MAX_DISPLAYED_UNSUPPORTED_ITEMS} 道暂不支持题目已省略"
        )
    return "\n".join(lines)


def _validate_multiple_choice(
    qi: QuestionInfo,
    display_question_num: Any,
    question_info: SurveyQuestionMeta | None,
    errors: list[str],
) -> bool:
    """Validate a multiple-choice entry. Returns True if the caller should skip remaining checks."""
    multi_min_limit: int | None = None
    if question_info:
        multi_min_limit = getattr(question_info, "multi_min_limit", None)

    probs = qi.details.custom_weights or qi.details.probabilities
    if isinstance(probs, list):
        positive_count = count_positive_weights(probs)
        if positive_count <= 0:
            errors.append(
                f"第 {display_question_num} 题（多选题）配置无效：\n"
                "  - 当前所有选项概率都小于等于 0%\n"
                "  - 请至少将 1 个选项的概率设为大于 0%"
            )
            return True
        if multi_min_limit is not None and multi_min_limit > 0 and positive_count < multi_min_limit:
            errors.append(
                f"第 {display_question_num} 题（多选题）配置冲突：\n"
                f"  - 题目要求最少选择 {multi_min_limit} 项\n"
                f"  - 但只有 {positive_count} 个选项的概率大于 0%\n"
                f"  - 请至少将 {multi_min_limit} 个选项的概率设为大于 0%"
            )
    return False


def _validate_text_entry(
    qi: QuestionInfo,
    display_question_num: Any,
    question_type: str,
    question_info: SurveyQuestionMeta | None,
    errors: list[str],
) -> None:
    if question_info is None or _is_text_ai_enabled(qi):
        return
    min_text_length = _extract_text_min_length(question_info.title, question_info.description)
    if min_text_length is None or min_text_length <= 0:
        return
    text_random_mode = (
        str(qi.details.answer_config.text_random_mode or "").strip().lower()
        if isinstance(qi.details.answer_config, TextQuestionAnswerConfig)
        else ""
    )
    if question_type == QuestionType.TEXT and text_random_mode not in ("", "none"):
        errors.append(
            f"第 {display_question_num} 题（填空题）配置冲突：\n"
            f"  - 题目要求答案最少 {min_text_length} 字\n"
            f"  - 当前选择的是{_text_random_mode_label(text_random_mode)}，无法保证达到字数要求\n"
            "  - 请改用足够长的答案列表，或启用 AI 作答"
        )
    elif question_type == QuestionType.TEXT:
        errors.append(
            f"第 {display_question_num} 题（填空题）配置冲突：\n"
            f"  - 题目要求答案最少 {min_text_length} 字\n"
            "  - 启用 AI 作答或选择随机模式以满足字数要求"
        )


def _validate_choice_weights(
    display_question_num: Any,
    question_type: str,
    configured_weights: Any,
    errors: list[str],
) -> None:
    if (
        isinstance(configured_weights, list)
        and configured_weights
        and count_positive_weights(configured_weights) <= 0
    ):
        errors.append(
            f"第 {display_question_num} 题（{question_type}）配置无效：\n"
            "  - 当前所有选项配比都小于等于 0\n"
            "  - 请至少将 1 个选项的配比设为大于 0"
        )


def _validate_matrix_entry(
    display_question_num: Any,
    configured_weights: Any,
    errors: list[str],
) -> None:
    invalid_rows = find_all_zero_matrix_rows(configured_weights)
    if invalid_rows == [0]:
        errors.append(
            f"第 {display_question_num} 题（矩阵题）配置无效：\n"
            "  - 当前所有选项配比都小于等于 0\n"
            "  - 请至少将 1 个选项的配比设为大于 0"
        )
    else:
        for row_idx in invalid_rows:
            errors.append(
                f"第 {display_question_num} 题（矩阵题）配置无效：\n"
                f"  - 第 {row_idx} 行所有选项配比都小于等于 0\n"
                "  - 请至少将 1 个选项的配比设为大于 0"
            )


def _validate_attached_selects(
    qi: QuestionInfo,
    display_question_num: Any,
    errors: list[str],
) -> None:
    if not isinstance(qi.details.answer_config, ChoiceQuestionAnswerConfig):
        return
    for cfg_idx, option_text in find_all_zero_attached_selects(
        qi.details.answer_config.attached_option_selects or []
    ):
        errors.append(
            f"第 {display_question_num} 题（嵌入式下拉）配置无效：\n"
            f"  - 第 {cfg_idx} 组（{option_text or '未命名选项'}）所有配比都小于等于 0\n"
            "  - 请至少将 1 个选项的配比设为大于 0"
        )


def validate_question_config(
    questions: list[QuestionInfo],
    questions_info: list[SurveyQuestionMeta | dict[str, Any]] | None = None,
) -> str | None:

    if not questions:
        return "未配置任何题目"

    errors: list[str] = []
    question_info_map, unsupported_questions = _build_question_info_map(questions_info)

    if unsupported_questions:
        return _format_unsupported_error(unsupported_questions)

    for idx, qi in enumerate(questions):
        question_num = qi.num if qi.num is not None else idx + 1
        question_type = qi.question_type
        try:
            normalized_question_num = int(question_num)
        except (ValueError, TypeError):
            normalized_question_num = idx + 1

        question_info = question_info_map.get(normalized_question_num)
        display_question_num = _display_question_num(question_num, question_info)

        if question_type == QuestionType.MULTIPLE:
            if _validate_multiple_choice(qi, display_question_num, question_info, errors):
                continue

        if question_type in TEXT_TYPES:
            _validate_text_entry(qi, display_question_num, question_type, question_info, errors)

        configured_weights = _pick_config_weights(qi)
        if question_type in CHOICE_TYPES:
            _validate_choice_weights(
                display_question_num, question_type, configured_weights, errors
            )

        if question_type == QuestionType.MATRIX:
            _validate_matrix_entry(display_question_num, configured_weights, errors)

        _validate_attached_selects(qi, display_question_num, errors)

    if errors:
        return "\n\n".join(errors)
    return None
