from __future__ import annotations

import copy
import os
from collections.abc import Sequence

from survey_submitter.core.config.schema import RuntimeConfig
from survey_submitter.core.questions.default_builder import build_default_question_entries
from survey_submitter.core.questions.schema import QuestionEntry, _infer_option_count
from survey_submitter.core.questions.types import CHOICE_TYPES, TEXT_TYPES, QuestionType, TypeCode
from survey_submitter.core.questions.validation import validate_question_config
from survey_submitter.core.reverse_fill.parser import (
    infer_reverse_fill_question_type,
    parse_choice_answer,
    parse_matrix_answer,
    parse_multi_text_answer,
    parse_text_answer,
    resolve_ordered_columns,
    resolve_question_entry,
    supports_reverse_fill_runtime,
)
from survey_submitter.core.reverse_fill.schema import (
    REVERSE_FILL_FORMAT_AUTO,
    REVERSE_FILL_FORMAT_WJX_SEQUENCE,
    REVERSE_FILL_STATUS_BLOCKED,
    REVERSE_FILL_STATUS_FALLBACK,
    REVERSE_FILL_STATUS_REVERSE,
    ReverseFillIssue,
    ReverseFillQuestionPlan,
    ReverseFillSampleRow,
    ReverseFillSpec,
    reverse_fill_format_label,
)
from survey_submitter.io.spreadsheets.wjx_excel import load_wjx_excel_export
from survey_submitter.providers.common import SURVEY_PROVIDER_WJX, normalize_survey_provider
from survey_submitter.providers.contracts import SurveyQuestionMeta, ensure_survey_question_meta

MAX_DISPLAYED_BLOCKING_ISSUES = 12


def _detail_from_columns(columns: list[object]) -> str:
    headers = [str(column.header or "").strip() for column in list(columns or []) if str(column.header or "").strip()]
    return " / ".join(headers)


def _regular_config_ready(entry: QuestionEntry | None, info: SurveyQuestionMeta | dict[str, object], expected_type: str) -> bool:
    if entry is None:
        return False
    entry_type = str(entry.question_type or "").strip()
    normalized_expected = str(expected_type or "").strip()
    if entry_type != normalized_expected:
        if normalized_expected == "location" and entry_type == "text" and bool(entry.is_location):
            pass  # text entry with is_location is compatible with location type
        else:
            return False
    copied_entry = copy.deepcopy(entry)
    copied_info = ensure_survey_question_meta(info)
    return validate_question_config([copied_entry], [copied_info]) is None


def _question_issue(
    *,
    question_num: int,
    title: str,
    category: str,
    reason: str,
    fallback_ready: bool,
    sample_rows: list[int] | None = None,
    suggestion: str | None = None,
    severity: str | None = None,
) -> ReverseFillIssue:
    resolved_suggestion = str(suggestion or "").strip()
    if not resolved_suggestion:
        resolved_suggestion = "已回退到当前题目的常规配置，可继续运行" if fallback_ready else "请先打开配置向导，把这题的常规配置补齐后再启动"
    resolved_severity = str(severity or "").strip().lower() or ("warn" if fallback_ready else "block")
    return ReverseFillIssue(
        question_num=question_num,
        title=title,
        severity=resolved_severity,
        category=category,
        reason=reason,
        suggestion=resolved_suggestion,
        sample_rows=list(sample_rows or []),
    )


def _build_question_plan(
    *,
    question_num: int,
    title: str,
    question_type: str,
    status: str,
    columns: list[object],
    detail: str,
    fallback_ready: bool,
    fallback_resolved: bool = False,
) -> ReverseFillQuestionPlan:
    headers = [str(column.header or "").strip() for column in list(columns or []) if str(column.header or "").strip()]
    return ReverseFillQuestionPlan(
        question_num=question_num,
        title=title,
        question_type=question_type,
        status=status,
        column_headers=headers,
        detail=detail,
        fallback_ready=bool(fallback_ready),
        fallback_resolved=bool(fallback_resolved),
    )


def _entry_differs_from_default(entry: QuestionEntry | None, default_entry: QuestionEntry | None) -> bool:
    if entry is None or default_entry is None:
        return False
    compare_fields = (
        "question_type",
        "probabilities",
        "texts",
        "rows",
        "option_count",
        "distribution_mode",
        "custom_weights",
        "ai_enabled",
        "multi_text_blank_modes",
        "multi_text_blank_ai_flags",
        "multi_text_blank_int_ranges",
        "text_random_mode",
        "text_random_int_range",
        "option_fill_texts",
        "fillable_option_indices",
        "attached_option_selects",
        "is_location",
        "dimension",
        "psycho_bias",
    )
    for field_name in compare_fields:
        left = copy.deepcopy(getattr(entry, field_name, None))
        right = copy.deepcopy(getattr(default_entry, field_name, None))
        if field_name == "option_count":
            left = int(left or _infer_option_count(entry) or 0)
            right = int(right or _infer_option_count(default_entry) or 0)
        elif field_name == "fillable_option_indices":
            left = list(left or [])
            right = list(right or [])
        if left != right:
            return True
    return False


def _build_no_sample_issue(*, start_row: int, total_samples: int) -> ReverseFillIssue:
    return ReverseFillIssue(
        question_num=0,
        title="样本总量",
        severity="block",
        category="sample_empty",
        reason=f"起始样本行设为 {start_row}，但 Excel 总共只有 {total_samples} 行数据，后面已经没有可反填的样本了",
        suggestion="请把起始样本行往前调，或更换包含更多数据的 Excel",
    )


def _build_global_issue(*, target_num: int, available_samples: int) -> ReverseFillIssue:
    return ReverseFillIssue(
        question_num=0,
        title="样本总量",
        severity="block",
        category="sample_shortage",
        reason=f"目标份数为 {target_num}，但从起始样本行开始只剩 {available_samples} 行可用样本",
        suggestion="请降低目标份数，或把起始样本行往前调，或更换样本更多的 Excel",
    )


def _append_question_issue_and_plan(
    *,
    issues: list[ReverseFillIssue],
    question_plans: list[ReverseFillQuestionPlan],
    question_num: int,
    title: str,
    question_type: str,
    columns: list[object],
    detail: str,
    category: str,
    reason: str,
    fallback_ready: bool,
    fallback_resolved: bool = False,
    sample_rows: list[int] | None = None,
    severity: str | None = None,
    suggestion: str | None = None,
) -> None:
    """Append a matching ``ReverseFillIssue`` and ``ReverseFillQuestionPlan`` pair."""
    issues.append(
        _question_issue(
            question_num=question_num,
            title=title,
            category=category,
            reason=reason,
            fallback_ready=fallback_ready,
            sample_rows=sample_rows,
            suggestion=suggestion,
            severity=severity,
        )
    )
    question_plans.append(
        _build_question_plan(
            question_num=question_num,
            title=title,
            question_type=question_type,
            status=REVERSE_FILL_STATUS_FALLBACK if fallback_ready else REVERSE_FILL_STATUS_BLOCKED,
            columns=columns,
            detail=detail,
            fallback_ready=fallback_ready,
            fallback_resolved=fallback_resolved,
        )
    )


def _validate_question_prerequisite(
    *,
    info: SurveyQuestionMeta,
    question_num: int,
    title: str,
) -> str | None:
    """Check unsupported or auto-handled question types.

    Returns error_reason if validation fails, None if OK to continue.
    """
    if bool(info.unsupported):
        return str(info.unsupported_reason or "当前程序暂不支持这道题").strip()
    return None


def _parse_answer_for_row(
    *,
    question_num: int,
    question_type: str,
    ordered_columns: list[object],
    raw_row: object,
    export: object,
    option_texts: list[str],
) -> object:
    """Parse answer from a single row.

    Raises ValueError on parse failure.
    """
    values_by_column = raw_row.values_by_column or {}

    if question_type in CHOICE_TYPES:
        return parse_choice_answer(
            question_num=question_num,
            question_type=question_type,
            raw_value=values_by_column.get(int(ordered_columns[0].column_index)),
            export_format=export.selected_format,
            option_texts=option_texts,
        )
    if question_type == QuestionType.TEXT:
        return parse_text_answer(
            question_num=question_num,
            raw_value=values_by_column.get(int(ordered_columns[0].column_index)),
        )
    if question_type == QuestionType.MULTI_TEXT:
        return parse_multi_text_answer(
            question_num=question_num,
            ordered_columns=ordered_columns,
            raw_row=raw_row,
        )
    if question_type == QuestionType.MATRIX:
        return parse_matrix_answer(
            question_num=question_num,
            ordered_columns=ordered_columns,
            raw_row=raw_row,
            export_format=export.selected_format,
            option_texts=option_texts,
        )
    return None


def _check_question_type_constraints(
    *,
    question_type: str,
    info: SurveyQuestionMeta,
    columns: list[object],
    export: object,
) -> dict[str, object] | None:
    """Check type-specific and runtime constraints.

    Returns error_dict if validation fails, None if OK to continue.
    """
    if question_type == QuestionType.ORDER:
        return {
            "detail": "排序题目前不参与反填覆盖",
            "category": "auto_handled",
            "reason": "排序题目目前不参与反填覆盖",
            "severity": "warn",
            "suggestion": "自动按常规逻辑处理（执行时自动随机排序）",
        }

    if not supports_reverse_fill_runtime(question_type, info):
        return {
            "detail": "当前题型或题目结构不在反填 V1 支持范围内",
            "category": "unsupported_type",
            "reason": "当前题型或题目结构不在反填 V1 支持范围内",
        }

    return None


def _check_column_mapping(
    *,
    question_type: str,
    columns: list[object],
    info: SurveyQuestionMeta,
    issues: list[ReverseFillIssue],
    question_plans: list[ReverseFillQuestionPlan],
    question_num: int,
    title: str,
    fallback_ready: bool,
    fallback_resolved: bool,
) -> list[object] | None:
    """Validate column mapping for a question.
    
    Returns ordered columns if valid, None if validation fails (and appends issue+plan).
    """
    # Check single-column types have exactly one column
    if question_type in CHOICE_TYPES | TEXT_TYPES and question_type != QuestionType.MULTI_TEXT and len(columns) != 1:
        _append_question_issue_and_plan(
            issues=issues, question_plans=question_plans,
            question_num=question_num, title=title, question_type=question_type,
            columns=columns, detail="这道题在 Excel 中对应了多列，V1 无法确认唯一答案列",
            category="mapping_ambiguous", reason="这道题在 Excel 中对应了多列，V1 无法确认唯一答案列",
            fallback_ready=fallback_ready, fallback_resolved=fallback_resolved,
        )
        return None

    # Resolve matrix columns
    if question_type == QuestionType.MATRIX:
        row_texts = list(info.row_texts or [])
        if row_texts and len(columns) != len(row_texts):
            _append_question_issue_and_plan(
                issues=issues, question_plans=question_plans,
                question_num=question_num, title=title, question_type=question_type,
                columns=columns, detail=f"矩阵题解析出 {len(row_texts)} 行，但 Excel 里只有 {len(columns)} 列",
                category="mapping_mismatch",
                reason=f"矩阵题解析出 {len(row_texts)} 行，但 Excel 里只有 {len(columns)} 列",
                fallback_ready=fallback_ready, fallback_resolved=fallback_resolved,
            )
            return None
        return resolve_ordered_columns(columns, row_texts)

    # Resolve multi-text columns
    if question_type == QuestionType.MULTI_TEXT:
        blank_labels = list(info.text_input_labels or [])
        if blank_labels and len(columns) != len(blank_labels):
            _append_question_issue_and_plan(
                issues=issues, question_plans=question_plans,
                question_num=question_num, title=title, question_type=question_type,
                columns=columns, detail=f"多项填空解析出 {len(blank_labels)} 个空，但 Excel 里只有 {len(columns)} 列",
                category="mapping_mismatch",
                reason=f"多项填空解析出 {len(blank_labels)} 个空，但 Excel 里只有 {len(columns)} 列",
                fallback_ready=fallback_ready, fallback_resolved=fallback_resolved,
            )
            return None
        return resolve_ordered_columns(columns, blank_labels)

    return columns


def _validate_and_collect_question(
    *,
    info: SurveyQuestionMeta,
    question_entries: list[QuestionEntry],
    default_entry_by_num: dict[int, QuestionEntry],
    export: object,
    selected_rows: list[object],
    answers_by_row: dict[int, dict[int, object]],
    issues: list[ReverseFillIssue],
    question_plans: list[ReverseFillQuestionPlan],
) -> bool:
    """Validate and process a single question.

    Returns True if question was successfully processed, False if skipped/error.
    """
    if info.type_code == TypeCode.DESCRIPTION:
        return False

    question_num = int(info.num or 0)
    if question_num <= 0:
        return False

    title = str(info.title or f"第{question_num}题").strip()
    entry = resolve_question_entry(info, question_entries)
    question_type = infer_reverse_fill_question_type(info, entry)
    columns = list((export.question_columns or {}).get(question_num) or [])
    fallback_ready = _regular_config_ready(entry, info, question_type)
    fallback_resolved = fallback_ready and _entry_differs_from_default(entry, default_entry_by_num.get(question_num))

    # Validation step 1: Prerequisites
    prereq_error = _validate_question_prerequisite(info=info, question_num=question_num, title=title)
    if prereq_error:
        _append_question_issue_and_plan(
            issues=issues, question_plans=question_plans,
            question_num=question_num, title=title, question_type=question_type,
            columns=columns, detail=prereq_error, category="runtime_unsupported",
            reason=prereq_error, fallback_ready=False,
            suggestion="这题不是反填没做，是程序本身还不能答，当前版本无法启动",
        )
        return False

    # Validation step 2: Type constraints
    type_error = _check_question_type_constraints(
        question_type=question_type,
        info=info,
        columns=columns,
        export=export,
    )
    if type_error:
        _append_question_issue_and_plan(
            issues=issues, question_plans=question_plans,
            question_num=question_num, title=title, question_type=question_type,
            columns=columns, detail=type_error["detail"], category=type_error["category"],
            reason=type_error["reason"], fallback_ready=fallback_ready,
            fallback_resolved=fallback_resolved,
            severity=type_error.get("severity"),
            suggestion=type_error.get("suggestion"),
        )
        return False

    # Validation step 3: Column mapping
    if not columns:
        _append_question_issue_and_plan(
            issues=issues, question_plans=question_plans,
            question_num=question_num, title=title, question_type=question_type,
            columns=columns, detail="Excel 中没有找到这道题对应的列",
            category="mapping_missing", reason="Excel 中没有找到这道题对应的列",
            fallback_ready=fallback_ready, fallback_resolved=fallback_resolved,
        )
        return False

    # Validation step 4: Column ordering
    ordered_columns = _check_column_mapping(
        question_type=question_type,
        columns=columns,
        info=info,
        issues=issues,
        question_plans=question_plans,
        question_num=question_num,
        title=title,
        fallback_ready=fallback_ready,
        fallback_resolved=fallback_resolved,
    )
    if ordered_columns is None:
        return False

    # Validation step 5: Parse answers
    parse_errors = _parse_question_answers(
        question_num=question_num,
        question_type=question_type,
        ordered_columns=ordered_columns,
        selected_rows=selected_rows,
        answers_by_row=answers_by_row,
        export=export,
        option_texts=list(info.option_texts or []),
    )

    if parse_errors:
        _handle_parse_errors(
            question_num=question_num,
            question_type=question_type,
            export=export,
            parse_errors=parse_errors,
            issues=issues,
            question_plans=question_plans,
            title=title,
            columns=columns,
            fallback_ready=fallback_ready,
            fallback_resolved=fallback_resolved,
            answers_by_row=answers_by_row,
        )
        return False

    # Question is successfully configured
    question_plans.append(
        _build_question_plan(
            question_num=question_num,
            title=title,
            question_type=question_type,
            status=REVERSE_FILL_STATUS_REVERSE,
            columns=ordered_columns,
            detail=f"来源列：{_detail_from_columns(ordered_columns)}",
            fallback_ready=False,
        )
    )
    return True


def _handle_parse_errors(
    *,
    question_num: int,
    question_type: str,
    export: object,
    parse_errors: list[int],
    issues: list[ReverseFillIssue],
    question_plans: list[ReverseFillQuestionPlan],
    title: str,
    columns: list[object],
    fallback_ready: bool,
    fallback_resolved: bool,
    answers_by_row: dict[int, dict[int, object]],
) -> None:
    """Handle parsing errors by adding issue and cleaning answers."""
    if question_type in CHOICE_TYPES | {QuestionType.MATRIX}:
        if export.selected_format == REVERSE_FILL_FORMAT_WJX_SEQUENCE:
            reason = "这道题在样本中出现了超范围序号或 V1 不支持的复合值"
        else:
            reason = "这道题在样本中出现了无法匹配选项的值或 V1 不支持的复合值"
    else:
        reason = "这道题在样本中出现了 V1 无法稳定回放的值"

    _append_question_issue_and_plan(
        issues=issues, question_plans=question_plans,
        question_num=question_num, title=title, question_type=question_type,
        columns=columns, detail=reason,
        category="unsupported_value", reason=reason,
        fallback_ready=fallback_ready, fallback_resolved=fallback_resolved,
        sample_rows=parse_errors[:3],
    )

    # Remove answers for this question from all rows
    for row_answers in answers_by_row.values():
        row_answers.pop(question_num, None)


def build_reverse_fill_spec(
    *,
    source_path: str,
    survey_provider: str,
    questions_info: Sequence[SurveyQuestionMeta | dict[str, object]],
    question_entries: list[QuestionEntry],
    selected_format: str = REVERSE_FILL_FORMAT_AUTO,
    start_row: int = 1,
    target_num: int = 0,
) -> ReverseFillSpec:
    provider = normalize_survey_provider(survey_provider, default=SURVEY_PROVIDER_WJX)
    if provider != SURVEY_PROVIDER_WJX:
        raise ValueError("反填 V1 目前只支持问卷星")
    if not questions_info:
        raise ValueError("当前还没有解析出问卷题目，无法校验反填")

    normalized_questions_info = [
        ensure_survey_question_meta(raw_info, index=info_index)
        for info_index, raw_info in enumerate(list(questions_info or []), start=1)
        if isinstance(raw_info, (dict, SurveyQuestionMeta))
    ]
    default_entries = build_default_question_entries(normalized_questions_info)
    default_entry_by_num = {
        int(entry.question_num or 0): entry
        for entry in list(default_entries or [])
        if int(entry.question_num or 0) > 0
    }

    export = load_wjx_excel_export(source_path, preferred_format=selected_format)
    normalized_start_row = max(1, int(start_row or 1))
    total_samples = int(export.total_data_rows or 0)
    available_rows = max(0, total_samples - normalized_start_row + 1)
    effective_target_num = max(0, int(target_num or 0))
    if effective_target_num <= 0:
        effective_target_num = available_rows
    selected_rows = list(export.raw_rows or [])[normalized_start_row - 1 :]

    issues: list[ReverseFillIssue] = []
    question_plans: list[ReverseFillQuestionPlan] = []
    answers_by_row: dict[int, dict[int, object]] = {
        int(row.data_row_number): {} for row in selected_rows
    }

    # Check global sample constraints
    if available_rows <= 0:
        issues.append(_build_no_sample_issue(start_row=normalized_start_row, total_samples=total_samples))
    elif target_num > 0 and target_num > available_rows:
        issues.append(_build_global_issue(target_num=target_num, available_samples=available_rows))

    # Process each question
    for info in normalized_questions_info:
        _validate_and_collect_question(
            info=info,
            question_entries=question_entries,
            default_entry_by_num=default_entry_by_num,
            export=export,
            selected_rows=selected_rows,
            answers_by_row=answers_by_row,
            issues=issues,
            question_plans=question_plans,
        )

    # Assemble final samples
    samples: list[ReverseFillSampleRow] = []
    for raw_row in selected_rows:
        answers = dict(answers_by_row.get(int(raw_row.data_row_number)) or {})
        samples.append(
            ReverseFillSampleRow(
                data_row_number=int(raw_row.data_row_number),
                worksheet_row_number=int(raw_row.worksheet_row_number),
                answers=answers,
            )
        )

    return ReverseFillSpec(
        source_path=os.path.abspath(str(source_path or "").strip()),
        selected_format=str(export.selected_format or REVERSE_FILL_FORMAT_AUTO),
        detected_format=str(export.detected_format or export.selected_format or REVERSE_FILL_FORMAT_AUTO),
        start_row=normalized_start_row,
        total_samples=total_samples,
        available_samples=available_rows,
        target_num=effective_target_num,
        question_plans=question_plans,
        issues=issues,
        samples=samples,
    )


def format_reverse_fill_blocking_message(spec: ReverseFillSpec) -> str:
    blocking = list(spec.blocking_issues)
    if not blocking:
        return ""
    lines = [
        f"反填配置校验失败（{reverse_fill_format_label(spec.detected_format)}）：",
    ]
    for issue in blocking[:MAX_DISPLAYED_BLOCKING_ISSUES]:
        prefix = "样本数量" if int(issue.question_num or 0) <= 0 else f"第 {issue.question_num} 题"
        lines.append(f"  - {prefix}：{issue.reason}")
        if issue.suggestion:
            lines.append(f"    {issue.suggestion}")
    if len(blocking) > MAX_DISPLAYED_BLOCKING_ISSUES:
        lines.append(f"  - 其余 {len(blocking) - MAX_DISPLAYED_BLOCKING_ISSUES} 个阻塞项已省略")
    return "\n".join(lines)


def build_enabled_reverse_fill_spec(
    config: RuntimeConfig,
    questions_info: list[SurveyQuestionMeta | dict[str, object]],
    question_entries: list[QuestionEntry],
) -> ReverseFillSpec | None:
    if not bool(config.reverse_fill_enabled):
        return None
    source_path = str(config.reverse_fill_source_path or "").strip()
    if not source_path:
        return None
    spec = build_reverse_fill_spec(
        source_path=source_path,
        survey_provider=str(config.survey_provider or SURVEY_PROVIDER_WJX),
        questions_info=list(questions_info or []),
        question_entries=list(question_entries or []),
        selected_format=str(config.reverse_fill_format or REVERSE_FILL_FORMAT_AUTO),
        start_row=max(1, int(config.reverse_fill_start_row or 1)),
        target_num=max(0, int(config.target or 0)),
    )
    if spec.blocking_issue_count > 0:
        raise ValueError(format_reverse_fill_blocking_message(spec))
    return spec
