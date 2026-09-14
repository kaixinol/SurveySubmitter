"""阶段三：题型解析模型。

分类结果决定调用哪个模型：选择 / 下拉 / 矩阵 / 滑块矩阵 / 滑块 / 量表。
每个模型只负责把 DOM 变成选项、行文本、可填空下标、多选限额。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from survey_submitter.core.questions.types import QuestionType
from survey_submitter.providers.match_utils import normalize_match_text

from ..questions.multiple_limits import (
    _extract_min_max_from_attributes,
    _extract_multi_limit_range_from_text,
    _extract_range_from_possible_json,
)
from ..regexes import (
    WJX_FORCE_SELECT_CLEAN_RE,
    WJX_FORCE_SELECT_COMMAND_RE,
    WJX_FORCE_SELECT_INDEX_TARGET_RE,
    WJX_FORCE_SELECT_LABEL_TARGET_RE,
    WJX_FORCE_SELECT_OPTION_LABEL_RE,
    WJX_FORCE_SELECT_SENTENCE_SPLIT_RE,
)
from .features import Marker
from .texts import (
    _normalize_html_text,
    attr_text,
    class_xpath,
    desc_or_self,
    first,
    has_content,
    looks_like_placeholder,
    nodes,
    option_text_from_attrs,
    text_input_control,
    text_of,
)

CHOICE_MODEL_TYPES = frozenset(
    {
        QuestionType.SINGLE,
        QuestionType.MULTIPLE,
        QuestionType.SCORE,
        QuestionType.SCALE,
        QuestionType.ORDER,
    }
)

_C_LABEL = class_xpath("label")
_XP_LABEL = f".//*[{_C_LABEL}]"
_XP_FALLBACK_OPTIONS = (f".//*[{_C_LABEL}]", ".//li//span", ".//li")
_UI_OTHER = desc_or_self(f"*[{class_xpath('ui-other')}]")
_XP_SHARED_TEXT = (
    f"{_UI_OTHER}//input|{_UI_OTHER}//textarea"
    "|.//input[contains(@id,'other')]|.//input[contains(@name,'other')]"
    "|.//textarea[contains(@id,'other')]|.//textarea[contains(@name,'other')]"
)
_XP_TEXT_INPUTS = ".//input|.//textarea"

_ROW_LABEL_CLASSES = (
    "label",
    "row-title",
    "rowtitle",
    "row",
    "item-title",
    "itemTitle",
    "itemTitleSpan",
    "stitle",
)
_ROW_TEXT_CLASSES = ("itemTitleSpan", "itemTitle", "item-title", "row-title")

_XP_RATING_ANCHORS = (
    f".//*[{class_xpath('scale-rating')}]//ul/li/a",
    f".//*[{class_xpath('scale-rating')}]//a[@val]",
    ".//ul[@tp='d']/li/a",
    ".//ul[contains(@class,'modlen')]/li/a",
)
_XP_SLIDER_INPUT = f".//input[{class_xpath('ui-slider-input')}]"
_XP_RULER = f".//*[{class_xpath('ruler')}]//*[{class_xpath('cm')}][@data-value]"
_TAIL_T = "substring(@id, string-length(@id)) = 't'"
_XP_ROWTITLE_SPAN = f".//tr[{class_xpath('rowtitletr')}]//*[{class_xpath('itemTitleSpan')}]"
_XP_ROWTITLE_TD = f".//tr[{class_xpath('rowtitletr')}]//td[{class_xpath('title')}]"
_XP_ROWTITLE_TAIL = f".//tr[{_TAIL_T}]//*[{class_xpath('itemTitleSpan')}]|.//tr[{_TAIL_T}]//td[{class_xpath('title')}]"

_LIMIT_TEXT_CLASSES = (
    "qtypetip",
    "topichtml",
    "field-label",
    "field-desc",
    "question-desc",
    "question-tip",
    "qtip",
    "qnotice",
    "question-hint",
)
_XP_LIMIT_STRIP = (
    f".//*[{class_xpath('ui-controlgroup')}]|.//ul|.//ol|.//table|.//textarea|.//select"
    f"|.//*[{class_xpath('slider')}]|.//*[{class_xpath('rangeslider')}]"
    f"|.//*[{class_xpath('range-slider')}]|.//*[{class_xpath('errorMessage')}]"
)

_XP_FORCE_FRAGMENTS = (
    class_xpath("qtypetip"),
    class_xpath("topichtml"),
    class_xpath("field-label"),
)
_SELECT_PLACEHOLDER_PREFIXES = ("请选择", "请先选择")
_CUSTOM_SELECT_KEYS = ("cusom", "custom", "data-custom", "data-cusom")
_CUSTOM_SELECT_SPLIT_RE = re.compile(r"[,，\n\r|/]+")
_SLIDER_MAX_VALUES = 200


@dataclass(slots=True)
class Options:
    option_texts: list[str] = field(default_factory=list)
    option_count: int = 0
    matrix_rows: int = 0
    row_texts: list[str] = field(default_factory=list)
    fillable_indices: list[int] = field(default_factory=list)
    required_fillable_indices: list[int] = field(default_factory=list)
    multi_min_limit: int | None = None
    multi_max_limit: int | None = None


def parse(features, type_code: str) -> Options:
    """按题型分派到对应解析模型。"""
    if type_code in CHOICE_MODEL_TYPES:
        return _choice_model(features, type_code)
    if type_code == QuestionType.DROPDOWN:
        return _dropdown_model(features)
    if type_code == QuestionType.MATRIX:
        return _matrix_model(features)
    if Marker.SLIDER_MATRIX in features.markers:
        return _slider_matrix_model(features)
    if type_code == QuestionType.SLIDER:
        return Options(option_count=1)
    return Options()


# --------------------------------------------------------------------- 共用判定


def _has_text_input(node, required: bool = False) -> bool:
    if node is None:
        return False
    if text_input_control(node) and (not required or node.get("required") is not None):
        return True
    return any(
        text_input_control(candidate) and (not required or candidate.get("required") is not None)
        for candidate in nodes(node, _XP_TEXT_INPUTS)
    )


def _has_shared_text_input(node, required: bool = False) -> bool:
    return any(_has_text_input(element, required) for element in nodes(node, _XP_SHARED_TEXT))


def placeholder_option(index: int, value: str | None, text: str) -> bool:
    if index != 0:
        return False
    normalized_value = _normalize_html_text(value)
    normalized_text = _normalize_html_text(text)
    if not normalized_text:
        return True
    if normalized_value in {"", "0", "-1", "-2"}:
        return True
    compact = normalized_text.replace(" ", "")
    return any(compact.startswith(prefix) for prefix in _SELECT_PLACEHOLDER_PREFIXES)


def _to_float(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------- 选择类模型


def _choice_model(features, type_code: str) -> Options:
    node = features.node
    texts: list[str] = []
    fillable: list[int] = []
    required_fillable: list[int] = []

    for element in features.option_nodes:
        label = first(element, _XP_LABEL)
        text = _normalize_html_text(text_of(label if label is not None else element))
        if not text:
            text = option_text_from_attrs(element)
        if not text:
            continue
        index = len(texts)
        texts.append(text)
        if _has_text_input(element):
            fillable.append(index)
            if _has_text_input(element, required=True):
                required_fillable.append(index)

    if not texts:
        seen: set[str] = set()
        for xpath in _XP_FALLBACK_OPTIONS:
            for element in nodes(node, xpath):
                text = _normalize_html_text(text_of(element))
                if not text:
                    text = option_text_from_attrs(element)
                if not text or text in seen:
                    continue
                texts.append(text)
                seen.add(text)
            if texts:
                break

    if not fillable and texts and _has_shared_text_input(node):
        fillable.append(len(texts) - 1)
        if _has_shared_text_input(node, required=True):
            required_fillable.append(len(texts) - 1)

    options = Options(
        option_texts=texts,
        option_count=len(texts),
        fillable_indices=sorted(set(fillable)),
        required_fillable_indices=sorted(set(required_fillable)),
    )
    if type_code == QuestionType.MULTIPLE:
        options.multi_min_limit, options.multi_max_limit = _multiple_limits(node)
    return options


def _dropdown_model(features) -> Options:
    select = first(features.node, ".//select")
    if select is None and features.number is not None:
        select = first(features.root, f'//select[@id="q{features.number}"]')
    if select is None:
        return Options()
    texts: list[str] = []
    for index, option in enumerate(nodes(select, ".//option")):
        text = _normalize_html_text(text_of(option))
        if placeholder_option(index, option.get("value"), text) or not text:
            continue
        texts.append(text)
    fillable = [len(texts) - 1] if texts and _has_shared_text_input(features.node) else []
    return Options(option_texts=texts, option_count=len(texts), fillable_indices=fillable)


# --------------------------------------------------------------------- 矩阵模型


def _row_label(row, cells) -> str:
    label = ""
    if cells:
        label = _normalize_html_text(text_of(cells[0]))
        if not label:
            label = attr_text(cells[0], ("data-original-title",))
    if not label:
        label = attr_text(row, ("data-original-title",))
    if not label:
        for name in _ROW_LABEL_CLASSES:
            element = first(row, f".//*[{class_xpath(name)}]")
            if element is not None:
                label = _normalize_html_text(text_of(element))
                if label:
                    break
    if not label:
        for child in nodes(row, ".//label|.//span|.//div|.//p")[:10]:
            label = attr_text(child, ("data-original-title",))
            if label:
                break
            label = _normalize_html_text(text_of(child))
            if label:
                break
    return label


def _row_index_rows(table) -> tuple[int, list[str]]:
    matrix_rows = 0
    row_texts: list[str] = []
    for row in nodes(table, ".//tr"):
        row_index = row.get("rowindex") or ""
        if not (row_index and str(row_index).isdigit()):
            continue
        matrix_rows += 1
        cells = nodes(row, ".//td|.//th")
        if cells:
            row_texts.append(_row_label(row, cells))
    return matrix_rows, row_texts


def _data_rows(table, number) -> tuple[int, list[str], list[str]]:
    header_id = f"drv{number}_1"
    data_rows: list[tuple[str, list]] = []
    for row in nodes(table, ".//tr"):
        if (row.get("id") or "") == header_id:
            continue
        cells = nodes(row, ".//td|.//th")
        if len(cells) <= 1:
            continue
        first_text = _row_label(row, cells)
        other_texts = [_normalize_html_text(text_of(cell)) for cell in cells[1:]]
        if not first_text and any(other_texts):
            continue
        data_rows.append((first_text, cells))
    option_texts: list[str] = []
    if data_rows:
        max_cols = max((len(cells) - 1 for _, cells in data_rows), default=0)
        if max_cols > 0:
            option_texts = [str(i + 1) for i in range(max_cols)]
    return len(data_rows), option_texts, [label for label, _ in data_rows]


def _from_input_names(node, number) -> tuple[int, list[str], list[str]]:
    pattern = re.compile(rf"q{number}[_-](\d+)(?:[_-](\d+))?")
    row_indices: list[int] = []
    col_indices: list[int] = []
    for item in nodes(node, ".//input"):
        raw_name = str(item.get("name") or item.get("id") or "")
        if not raw_name:
            continue
        match = pattern.search(raw_name)
        if not match:
            continue
        try:
            row_indices.append(int(match.group(1)))
        except (ValueError, TypeError):
            pass
        if match.group(2):
            try:
                col_indices.append(int(match.group(2)))
            except (ValueError, TypeError):
                pass
    matrix_rows = max(row_indices) if row_indices else 0
    row_texts = [""] * matrix_rows if row_indices else []
    option_texts: list[str] = []
    if col_indices:
        max_cols = max(col_indices)
        if max_cols > 0:
            option_texts = [str(i + 1) for i in range(max_cols)]
    return matrix_rows, option_texts, row_texts


def _fill_row_texts(node, matrix_rows: int, row_texts: list[str]) -> tuple[int, list[str]]:
    candidates: list[str] = []
    for name in _ROW_TEXT_CLASSES:
        found = nodes(node, f".//*[{class_xpath(name)}]")
        if found:
            candidates = [
                text for text in (_normalize_html_text(text_of(item)) for item in found) if text
            ]
            if candidates:
                break
    if not candidates:
        return matrix_rows, row_texts
    if matrix_rows <= 0:
        return len(candidates), list(candidates)
    merged = list(row_texts)
    for index in range(min(len(candidates), len(merged))):
        if not merged[index]:
            merged[index] = candidates[index]
    return matrix_rows, merged


def _header_texts(table) -> list[str]:
    best_texts: list[str] = []
    best_score = 0
    for row in nodes(table, ".//tr"):
        if first(row, ".//input|.//select|.//textarea") is not None:
            continue
        cells = nodes(row, ".//td|.//th")
        if len(cells) <= 1:
            continue
        non_empty = [
            text for text in (_normalize_html_text(text_of(cell)) for cell in cells) if text
        ]
        if len(non_empty) < 2:
            continue
        if len(non_empty) > best_score:
            best_score = len(non_empty)
            best_texts = non_empty
    return best_texts


def _dedupe(option_texts: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in option_texts:
        text = _normalize_html_text(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _matrix_model(features) -> Options:
    node = features.node
    table = features.matrix_table
    matrix_rows = 0
    option_texts: list[str] = []
    row_texts: list[str] = []

    if table is not None:
        matrix_rows, row_texts = _row_index_rows(table)
    if matrix_rows == 0 and table is not None:
        matrix_rows, option_texts, row_texts = _data_rows(table, features.number)
    if matrix_rows == 0 and node is not None:
        matrix_rows, option_texts, row_texts = _from_input_names(node, features.number)
    if node is not None and (not row_texts or any(not text for text in row_texts)):
        matrix_rows, row_texts = _fill_row_texts(node, matrix_rows, row_texts)
    if not option_texts and table is not None:
        option_texts = _header_texts(table)

    raw_option_texts = list(option_texts)
    option_texts = _dedupe(option_texts)
    if not option_texts:
        fallback_columns = len([text for text in raw_option_texts if _normalize_html_text(text)])
        if fallback_columns > 0:
            option_texts = [str(i + 1) for i in range(fallback_columns)]
    return Options(
        option_texts=option_texts,
        option_count=len(option_texts),
        matrix_rows=matrix_rows,
        row_texts=row_texts,
    )


# --------------------------------------------------------------------- 滑块模型


def _format_slider_value(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return str(round(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _slider_values(slider_input) -> list[str]:
    min_value = _to_float(slider_input.get("min"))
    max_value = _to_float(slider_input.get("max"))
    if min_value is None or max_value is None:
        return []
    step_value = _to_float(slider_input.get("step"))
    if step_value is None or step_value <= 0:
        step_value = 1.0
    if max_value < min_value:
        min_value, max_value = max_value, min_value
    values: list[str] = []
    current = min_value
    while current <= max_value + 1e-9 and len(values) < _SLIDER_MAX_VALUES:
        values.append(_format_slider_value(current))
        current += step_value
    return values


def _slider_matrix_model(features) -> Options:
    node = features.node
    row_titles = (
        nodes(node, _XP_ROWTITLE_SPAN)
        or nodes(node, _XP_ROWTITLE_TD)
        or nodes(node, _XP_ROWTITLE_TAIL)
    )
    row_texts = [
        text for text in (_normalize_html_text(text_of(item)) for item in row_titles) if text
    ]

    option_texts: list[str] = []
    seen: set[str] = set()
    for scale_node in nodes(node, _XP_RULER):
        value = _normalize_html_text(scale_node.get("data-value") or "")
        if value and value not in seen:
            seen.add(value)
            option_texts.append(value)
    if not option_texts and features.slider_inputs:
        option_texts = _slider_values(features.slider_inputs[0])

    matrix_rows = len(features.slider_inputs) if features.slider_inputs else len(row_texts)
    if matrix_rows <= 0:
        matrix_rows = len(nodes(node, ".//tr[starts-with(@id,'drv')]"))
    return Options(
        option_texts=option_texts,
        option_count=len(option_texts),
        matrix_rows=matrix_rows,
        row_texts=row_texts,
    )


def slider_range(features) -> tuple[float | None, float | None, float | None]:
    node = features.node
    slider_input = first(node, f'.//input[@id="q{features.number}"]')
    if slider_input is None:
        slider_input = first(node, ".//input[@type='range']")
    if slider_input is None:
        slider_input = first(node, _XP_SLIDER_INPUT)
    if slider_input is None:
        return None, None, None
    return (
        _to_float(slider_input.get("min")),
        _to_float(slider_input.get("max")),
        _to_float(slider_input.get("step")),
    )


# --------------------------------------------------------------------- 量表修正


def _rating_texts(node) -> list[str]:
    anchors: list = []
    for xpath in _XP_RATING_ANCHORS:
        anchors = nodes(node, xpath)
        if anchors:
            break
    texts: list[str] = []
    seen: set[str] = set()
    for index, anchor in enumerate(anchors):
        text = option_text_from_attrs(anchor)
        if not has_content(text):
            text = _normalize_html_text(text_of(anchor))
        if not has_content(text):
            text = _normalize_html_text(anchor.get("title") or "")
        if not has_content(text):
            text = _normalize_html_text(anchor.get("val") or "")
        if not has_content(text):
            text = str(index + 1)
        if text in seen:
            continue
        seen.add(text)
        texts.append(text)
    return texts


def apply_rating_scale(
    node,
    type_code: str,
    option_texts: list[str],
    option_count: int,
    rating_hit: bool,
    rating_max: int,
) -> tuple[list[str], int]:
    """量表 / 评分题用真实锚点文本覆盖选项。"""
    if rating_hit:
        rating_texts = _rating_texts(node)
        if rating_texts:
            option_texts = rating_texts
        option_count = max(option_count, rating_max, len(option_texts))
        if option_count > 0:
            meaningful = any(has_content(text) for text in option_texts)
            if not option_texts or not meaningful:
                option_texts = [str(i + 1) for i in range(option_count)]
    elif type_code in {QuestionType.SCORE, QuestionType.SCALE}:
        scale_texts = _rating_texts(node)
        if scale_texts:
            option_texts = scale_texts
            option_count = len(scale_texts)
    return option_texts, option_count


# --------------------------------------------------------------------- 多选限额


def _limit_fragments(node) -> list[str]:
    fragments: list[str] = []
    for name in _LIMIT_TEXT_CLASSES:
        for element in nodes(node, f".//*[{class_xpath(name)}]"):
            text = _normalize_html_text(text_of(element))
            if text:
                fragments.append(text)

    cloned = copy.deepcopy(node)
    for element in cloned.xpath(_XP_LIMIT_STRIP):
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    cleaned = _normalize_html_text(text_of(cloned))
    if cleaned:
        fragments.append(cleaned)

    deduped: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        if not fragment or fragment in seen:
            continue
        seen.add(fragment)
        deduped.append(fragment)
    return deduped


def _multiple_limits(node) -> tuple[int | None, int | None]:
    if node is None:
        return None, None
    min_limit, max_limit = _extract_min_max_from_attributes(node)

    if min_limit is None or max_limit is None:
        for attr_name in ("data", "data-setting", "data-validate"):
            cand_min, cand_max = _extract_range_from_possible_json(node.get(attr_name))
            if min_limit is None and cand_min is not None:
                min_limit = cand_min
            if max_limit is None and cand_max is not None:
                max_limit = cand_max
            if min_limit is not None and max_limit is not None:
                break

    if min_limit is None or max_limit is None:
        for fragment in _limit_fragments(node):
            cand_min, cand_max = _extract_multi_limit_range_from_text(fragment)
            if min_limit is None and cand_min is not None:
                min_limit = cand_min
            if max_limit is None and cand_max is not None:
                max_limit = cand_max
            if min_limit is not None and max_limit is not None:
                break

    if min_limit is not None and max_limit is not None and min_limit > max_limit:
        min_limit, max_limit = max_limit, min_limit
    return min_limit, max_limit


# --------------------------------------------------------------------- 选项附加下拉


def _select_option_texts(select_element) -> list[str]:
    if select_element is None:
        return []
    texts: list[str] = []
    for index, option in enumerate(nodes(select_element, ".//option")):
        text = _normalize_html_text(text_of(option))
        if placeholder_option(index, option.get("value"), text) or not text:
            continue
        texts.append(text)
    return texts


def _custom_select_texts(element) -> list[str]:
    if element is None:
        return []
    options: list[str] = []
    for key in _CUSTOM_SELECT_KEYS:
        raw = element.get(key)
        if raw is None:
            continue
        for part in _CUSTOM_SELECT_SPLIT_RE.split(str(raw)):
            text = _normalize_html_text(part)
            if not text or looks_like_placeholder(text):
                continue
            options.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for option in options:
        if option in seen:
            continue
        seen.add(option)
        deduped.append(option)
    return deduped


def attached_selects(features) -> list[dict]:
    """选项上挂载的二级下拉（选项关联）。"""
    attached: list[dict] = []
    for option_index, element in enumerate(features.option_nodes):
        label = first(element, _XP_LABEL)
        option_text = _normalize_html_text(text_of(label)) if label is not None else ""
        if not option_text:
            option_text = option_text_from_attrs(element)
        select_options = _select_option_texts(first(element, ".//select"))
        if not select_options:
            for input_element in nodes(element, ".//input"):
                select_options = _custom_select_texts(input_element)
                if select_options:
                    break
        if not select_options:
            continue
        attached.append(
            {
                "option_index": option_index,
                "option_text": option_text,
                "select_options": select_options,
                "select_option_count": len(select_options),
            }
        )
    return attached


# --------------------------------------------------------------------- 强制选中项


def _normalize_force_text(value) -> str:
    text = normalize_match_text(value)
    if not text:
        return ""
    return WJX_FORCE_SELECT_CLEAN_RE.sub("", text).lower()


def _force_option_label(option_text) -> str | None:
    text = normalize_match_text(option_text)
    if not text:
        return None
    match = WJX_FORCE_SELECT_OPTION_LABEL_RE.match(text)
    if not match:
        return None
    label = str(match.group("label") or "").strip().upper()
    return label or None


def _force_fragments(features, title_text: str) -> list[str]:
    fragments: list[str] = []
    cleaned_title = _normalize_html_text(title_text)
    if cleaned_title:
        fragments.append(cleaned_title)
    for predicate in _XP_FORCE_FRAGMENTS:
        element = first(features.node, f".//*[{predicate}]")
        if element is None:
            continue
        text = _normalize_html_text(text_of(element))
        if text:
            fragments.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        key = _normalize_html_text(fragment)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def forced_option(
    features, title_text: str, option_texts: list[str]
) -> tuple[int | None, str | None]:
    """识别题干里“请选择第 N 项 / 请选择 A 项”这类强制指令。"""
    if not option_texts:
        return None, None
    normalized_options: list[tuple[int, str, str]] = []
    for index, option_text in enumerate(option_texts):
        normalized = _normalize_force_text(option_text)
        if not normalized:
            continue
        normalized_options.append((index, str(option_text or "").strip(), normalized))
    if not normalized_options:
        return None, None

    for fragment in _force_fragments(features, title_text):
        for command in WJX_FORCE_SELECT_COMMAND_RE.finditer(fragment):
            tail_text = fragment[command.end() :]
            if not tail_text:
                continue
            sentence = WJX_FORCE_SELECT_SENTENCE_SPLIT_RE.split(tail_text, maxsplit=1)[0]
            sentence = sentence.strip(" ：:，,、")
            if not sentence:
                continue
            normalized_sentence = normalize_match_text(sentence)
            compact_sentence = _normalize_force_text(sentence)
            if not compact_sentence:
                continue

            index_match = WJX_FORCE_SELECT_INDEX_TARGET_RE.fullmatch(normalized_sentence)
            if index_match:
                try:
                    target_index = int(index_match.group("index")) - 1
                except (ValueError, TypeError):
                    target_index = -1
                if 0 <= target_index < len(option_texts):
                    selected = str(option_texts[target_index] or "").strip()
                    return target_index, selected or None

            label_match = WJX_FORCE_SELECT_LABEL_TARGET_RE.fullmatch(compact_sentence)
            if label_match:
                target_label = str(label_match.group("label") or "").strip().upper()
                if target_label:
                    for index, raw_text, _ in normalized_options:
                        if _force_option_label(raw_text) == target_label:
                            return index, raw_text

            exact = sorted(
                (
                    item
                    for item in normalized_options
                    if not item[2].isdigit() and item[2] == compact_sentence
                ),
                key=lambda item: len(item[2]),
                reverse=True,
            )
            if exact:
                index, raw_text, _ = exact[0]
                return index, raw_text
    return None, None


__all__ = [
    "CHOICE_MODEL_TYPES",
    "Options",
    "apply_rating_scale",
    "attached_selects",
    "forced_option",
    "parse",
    "slider_range",
]
