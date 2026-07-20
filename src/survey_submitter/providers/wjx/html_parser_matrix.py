from __future__ import annotations

import re

from .html_parser_common import _normalize_html_text


def _postprocess_matrix_option_texts(option_texts: list[str]) -> list[str]:

    if not option_texts:
        return []
    cleaned: list[str] = []
    seen = set()
    for raw_text in option_texts:
        text = _normalize_html_text(raw_text)
        if not text:
            continue

        if text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _extract_matrix_header_texts(table) -> list[str]:

    if table is None:
        return []

    best_texts: list[str] = []
    best_score = 0
    rows = table.find_all("tr")
    for row in rows:
        if row.find(["input", "select", "textarea"]):
            continue
        cells = row.find_all(["td", "th"])
        if len(cells) <= 1:
            continue
        raw_texts = [_normalize_html_text(cell.get_text(" ", strip=True)) for cell in cells]
        non_empty_texts = [text for text in raw_texts if text]
        score = len(non_empty_texts)
        if score < 2:
            continue

        if score > best_score:
            best_score = score
            best_texts = non_empty_texts
    return best_texts


def _extract_attr_text(node) -> str:
    if node is None:
        return ""
    keys = (
        "title",
        "data-title",
        "data-text",
        "data-label",
        "aria-label",
        "alt",
        "htitle",
        "data-original-title",
    )
    for key in keys:
        raw = node.get(key)
        if raw is None:
            continue
        text_value = _normalize_html_text(str(raw))
        if text_value:
            return text_value
    return ""


def _extract_row_label(row, cells) -> str:
    label_text = ""
    if cells:
        label_text = _normalize_html_text(cells[0].get_text(" ", strip=True))
        if not label_text:
            label_text = _extract_attr_text(cells[0])
    if not label_text:
        label_text = _extract_attr_text(row)
    if not label_text:
        for selector in (
            ".label",
            ".row-title",
            ".rowtitle",
            ".row",
            ".item-title",
            ".itemTitle",
            ".itemTitleSpan",
            ".stitle",
        ):
            node = row.select_one(selector)
            if node:
                label_text = _normalize_html_text(node.get_text(" ", strip=True))
                if label_text:
                    break
    if not label_text:
        for child in row.find_all(["label", "span", "div", "p"], limit=10):
            label_text = _extract_attr_text(child)
            if label_text:
                break
            label_text = _normalize_html_text(child.get_text(" ", strip=True))
            if label_text:
                break
    return label_text


def _find_matrix_table(soup, question_div, question_number: int):
    table = None
    if question_div is not None:
        table = question_div.find(id=f"divRefTab{question_number}")
    if table is None and soup:
        table = soup.find(id=f"divRefTab{question_number}")
    return table


def _collect_rowindex_rows(table) -> tuple[int, list[str]]:
    matrix_rows = 0
    row_texts: list[str] = []
    for row in table.find_all("tr"):
        row_index = (row.get("rowindex") or "")
        if row_index and str(row_index).isdigit():
            matrix_rows += 1
            cells = row.find_all(["td", "th"])
            if cells:
                label_text = _extract_row_label(row, cells)
                row_texts.append(label_text)
    return matrix_rows, row_texts


def _collect_data_rows(table, question_number: int) -> tuple[int, list[str], list[str]]:
    data_rows = []
    header_id = f"drv{question_number}_1"
    for row in table.find_all("tr"):
        row_id = (row.get("id") or "")
        if row_id == header_id:
            continue
        cells = row.find_all(["td", "th"])
        if len(cells) <= 1:
            continue
        first_text = _extract_row_label(row, cells)
        other_texts = [_normalize_html_text(cell.get_text(" ", strip=True)) for cell in cells[1:]]
        if not first_text and any(other_texts):
            continue
        data_rows.append((first_text, cells))
    matrix_rows = len(data_rows)
    row_texts = [label for label, _ in data_rows]
    option_texts: list[str] = []
    if data_rows:
        max_cols = 0
        for _, cells in data_rows:
            max_cols = max(max_cols, max(0, len(cells) - 1))
        if max_cols > 0:
            option_texts = [str(i + 1) for i in range(max_cols)]
    return matrix_rows, option_texts, row_texts


def _collect_from_input_names(
    question_div,
    question_number: int,
) -> tuple[int, list[str], list[str]]:
    inputs = question_div.find_all("input")
    row_indices: list[int] = []
    col_indices: list[int] = []
    name_pattern = re.compile(rf"q{question_number}[_-](\d+)(?:[_-](\d+))?")
    for item in inputs:
        raw_name = str(item.get("name") or item.get("id") or "")
        if not raw_name:
            continue
        match = name_pattern.search(raw_name)
        if not match:
            continue
        try:
            row_idx = int(match.group(1))
            row_indices.append(row_idx)
        except (ValueError, TypeError):
            pass
        if match.group(2):
            try:
                col_idx = int(match.group(2))
                col_indices.append(col_idx)
            except (ValueError, TypeError):
                pass
    matrix_rows = 0
    row_texts: list[str] = []
    option_texts: list[str] = []
    if row_indices:
        matrix_rows = max(row_indices)
        row_texts = [""] * matrix_rows
    if col_indices:
        max_cols = max(col_indices)
        if max_cols > 0:
            option_texts = [str(i + 1) for i in range(max_cols)]
    return matrix_rows, option_texts, row_texts


def _fill_row_texts_from_selectors(
    question_div, matrix_rows: int, row_texts: list[str]
) -> tuple[int, list[str]]:
    candidates = []
    for selector in (".itemTitleSpan", ".itemTitle", ".item-title", ".row-title"):
        nodes = question_div.select(selector)
        if nodes:
            candidates = [_normalize_html_text(node.get_text(" ", strip=True)) for node in nodes]
            candidates = [text for text in candidates if text]
            if candidates:
                break
    if not candidates:
        return matrix_rows, row_texts
    if matrix_rows <= 0:
        matrix_rows = len(candidates)
        row_texts = list(candidates)
    else:
        merged: list[str] = list(row_texts)
        for idx in range(min(len(candidates), len(merged))):
            if not merged[idx]:
                merged[idx] = candidates[idx]
        row_texts = merged
    return matrix_rows, row_texts


def _collect_matrix_option_texts(
    soup, question_div, question_number: int
) -> tuple[int, list[str], list[str]]:
    table = _find_matrix_table(soup, question_div, question_number)

    matrix_rows = 0
    option_texts: list[str] = []
    row_texts: list[str] = []

    if table:
        matrix_rows, row_texts = _collect_rowindex_rows(table)

    if matrix_rows > 0:
        pass
    elif table:
        matrix_rows, option_texts, row_texts = _collect_data_rows(table, question_number)

    if matrix_rows == 0 and question_div is not None:
        matrix_rows, option_texts, row_texts = _collect_from_input_names(
            question_div,
            question_number,
        )

    if question_div is not None and (not row_texts or any(not text for text in row_texts)):
        matrix_rows, row_texts = _fill_row_texts_from_selectors(
            question_div,
            matrix_rows,
            row_texts,
        )

    if not option_texts and table:
        option_texts = _extract_matrix_header_texts(table)

    raw_option_texts = list(option_texts)
    option_texts = _postprocess_matrix_option_texts(option_texts)
    if not option_texts:
        fallback_columns = len([text for text in raw_option_texts if _normalize_html_text(text)])
        if fallback_columns > 0:
            option_texts = [str(i + 1) for i in range(fallback_columns)]

    return matrix_rows, option_texts, row_texts


def _extract_slider_range(
    question_div, question_number: int
) -> tuple[float | None, float | None, float | None]:

    slider_input = question_div.find("input", id=f"q{question_number}")
    if not slider_input:
        slider_input = question_div.find("input", attrs={"type": "range"})
    if not slider_input:
        slider_input = question_div.find(
            "input", class_=lambda value: value and "ui-slider-input" in str(value)
        )

    def _parse(raw: str | float | int | None) -> float | None:
        if raw is None:
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    if slider_input:
        return (
            _parse(slider_input.get("min")),
            _parse(slider_input.get("max")),
            _parse(slider_input.get("step")),
        )
    return None, None, None


def _question_div_looks_like_slider_matrix(question_div) -> bool:

    if question_div is None:
        return False
    slider_inputs = question_div.select("input.ui-slider-input[rowid]")
    if len(slider_inputs) < 2:
        return False
    slider_tracks = question_div.select(".rangeslider, .range-slider, .wjx-slider")
    return len(slider_tracks) >= len(slider_inputs)


def _format_slider_matrix_value(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _build_slider_matrix_option_texts_from_input(slider_input) -> list[str]:
    def _parse(raw: str | float | int | None) -> float | None:
        if raw is None:
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    min_value = _parse(slider_input.get("min"))
    max_value = _parse(slider_input.get("max"))
    step_value = _parse(slider_input.get("step"))
    if min_value is None or max_value is None:
        return []
    if step_value is None or step_value <= 0:
        step_value = 1.0
    if max_value < min_value:
        min_value, max_value = max_value, min_value
    max_count = 200
    values: list[str] = []
    current = min_value
    while current <= max_value + 1e-9 and len(values) < max_count:
        values.append(_format_slider_matrix_value(current))
        current += step_value
    return values


def _collect_slider_matrix_metadata(question_div) -> tuple[int, list[str], list[str]]:

    if question_div is None:
        return 0, [], []

    row_texts: list[str] = []
    option_texts: list[str] = []

    row_titles = question_div.select("tr.rowtitletr .itemTitleSpan")
    if not row_titles:
        row_titles = question_div.select("tr.rowtitletr td.title")
    if not row_titles:
        row_titles = question_div.select("tr[id$='t'] .itemTitleSpan, tr[id$='t'] td.title")
    for title in row_titles:
        text = _normalize_html_text(title.get_text(" ", strip=True))
        if text:
            row_texts.append(text)

    scale_nodes = question_div.select(".ruler .cm[data-value]")
    seen_values = set()
    for node in scale_nodes:
        value = _normalize_html_text(node.get("data-value") or "")
        if value and value not in seen_values:
            seen_values.add(value)
            option_texts.append(value)

    slider_inputs = question_div.select("input.ui-slider-input[rowid]")
    if not option_texts and slider_inputs:
        option_texts = _build_slider_matrix_option_texts_from_input(slider_inputs[0])

    matrix_rows = len(slider_inputs) if slider_inputs else len(row_texts)
    if matrix_rows <= 0:
        matrix_rows = len(question_div.select("tr[id^='drv']"))

    return matrix_rows, option_texts, row_texts
