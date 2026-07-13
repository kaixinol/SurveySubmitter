from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from python_calamine import CalamineWorkbook

from survey_submitter.core.reverse_fill.schema import (
    REVERSE_FILL_FORMAT_AUTO,
    REVERSE_FILL_FORMAT_WJX_SCORE,
    REVERSE_FILL_FORMAT_WJX_SEQUENCE,
    REVERSE_FILL_FORMAT_WJX_TEXT,
    ReverseFillColumn,
    ReverseFillRawRow,
    WjxExcelExport,
)

_QUESTION_HEADER_RE = re.compile(r"^\s*(\d+)\s*[、,.，．]\s*(.*?)\s*$")
_SEQUENCE_SUFFIX_RE = re.compile(r"^\(\s*选项\s*\d+\s*\)$")


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _normalize_cell_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _iter_question_values(
    raw_rows: Iterable[ReverseFillRawRow], question_columns: dict[int, list[ReverseFillColumn]]
) -> Iterable[Any]:
    column_indexes: list[int] = []
    for columns in question_columns.values():
        for column in columns:
            column_indexes.append(int(column.column_index))
    seen = set(column_indexes)
    if not seen:
        return []
    collected: list[Any] = []
    for row in raw_rows:
        values_by_column = row.values_by_column if isinstance(row.values_by_column, dict) else {}
        for column_index in column_indexes:
            collected.append(values_by_column.get(column_index))
    return collected


def _detect_wjx_export_format(
    question_columns: dict[int, list[ReverseFillColumn]], raw_rows: list[ReverseFillRawRow]
) -> str:
    for columns in question_columns.values():
        if len(columns) <= 1:
            continue
        if any(_SEQUENCE_SUFFIX_RE.match(str(column.suffix or "").strip()) for column in columns):
            return REVERSE_FILL_FORMAT_WJX_SEQUENCE

    has_numeric_type = False
    has_numeric_string = False
    has_string_marker = False
    for value in _iter_question_values(raw_rows[:5], question_columns):
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            has_numeric_type = True
            continue
        text = _cell_text(value)
        if not text:
            continue
        if "〖" in text and "〗" in text:
            has_string_marker = True
        if re.fullmatch(r"\d+(?:\.0+)?", text):
            has_numeric_string = True
            continue
        has_string_marker = True
    if has_string_marker and not has_numeric_type and has_numeric_string:
        return REVERSE_FILL_FORMAT_WJX_TEXT
    if has_numeric_type:
        return REVERSE_FILL_FORMAT_WJX_SCORE
    return REVERSE_FILL_FORMAT_WJX_TEXT


def load_wjx_excel_export(
    source_path: str, *, preferred_format: str = REVERSE_FILL_FORMAT_AUTO
) -> WjxExcelExport:
    raw_path = str(source_path or "").strip()
    if not raw_path:
        raise ValueError("未提供 Excel 文件路径")
    path = str(Path(raw_path).resolve())
    if not Path(path).exists():
        raise ValueError(f"Excel 文件不存在：{path}")

    workbook = CalamineWorkbook.from_path(path)
    if not workbook.sheet_names:
        raise ValueError("Excel 中没有可读取的工作表")
    sheet = workbook.get_sheet_by_index(0)
    all_rows = sheet.to_python()
    if not all_rows:
        raise ValueError("Excel 缺少表头，无法识别问卷列")

    header_row = all_rows[0]
    data_rows = all_rows[1:]

    question_columns: dict[int, list[ReverseFillColumn]] = {}
    for col_idx, value in enumerate(header_row):
        header = _cell_text(value)
        match = _QUESTION_HEADER_RE.match(header)
        if not match:
            continue
        question_num = int(match.group(1))
        suffix = str(match.group(2) or "").strip()
        column_index = col_idx + 1  # 1-based column index
        question_columns.setdefault(question_num, []).append(
            ReverseFillColumn(
                column_index=column_index,
                header=header,
                question_num=question_num,
                suffix=suffix,
            )
        )

    raw_rows: list[ReverseFillRawRow] = []
    for data_row_number, data_row in enumerate(data_rows, start=1):
        values_by_column: dict[int, Any] = {}
        for column_list in question_columns.values():
            for column in column_list:
                position = int(column.column_index) - 1
                raw_value = (
                    _normalize_cell_value(data_row[position]) if position < len(data_row) else None
                )
                values_by_column[int(column.column_index)] = raw_value
        raw_rows.append(
            ReverseFillRawRow(
                data_row_number=data_row_number,
                worksheet_row_number=data_row_number + 1,
                values_by_column=values_by_column,
            )
        )

    detected_format = _detect_wjx_export_format(question_columns, raw_rows)
    selected_format = str(preferred_format or REVERSE_FILL_FORMAT_AUTO).strip().lower()
    if selected_format == REVERSE_FILL_FORMAT_AUTO:
        selected_format = detected_format
    return WjxExcelExport(
        source_path=path,
        detected_format=detected_format,
        selected_format=selected_format,
        header_row_number=1,
        total_data_rows=len(raw_rows),
        question_columns=question_columns,
        raw_rows=raw_rows,
    )
