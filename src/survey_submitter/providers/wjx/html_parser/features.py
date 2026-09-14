"""阶段一：特征抽取。

对单个题目 div 只做一轮 XPath 查询，把后续分类与解析要用到的 DOM 信号
（结构标记、节点集合、文本输入、量表锚点等）一次性抽成 :class:`Features`。

旧实现里 ``find_all`` / ``select`` 在十余个函数里反复遍历同一棵子树，
这里收敛成一次查询 + 纯内存判定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from ..regexes import WJX_MODLEN_CLASS_RE, WJX_QUESTION_PREFIX_RE
from .texts import (
    _TEXT_INPUT_TYPES,
    _normalize_html_text,
    class_xpath,
    classes_of,
    first,
    in_class,
    nodes,
    text_of,
)


class Marker(StrEnum):
    """从题目 div 抽出的信号标记。

    取代散落的布尔字段：特征抽取阶段只产出这个集合，
    题型分类阶段用规则表去匹配它。
    """

    HIDDEN = "hidden"  # 自身或祖先被隐藏
    REQUIRED = "required"
    GAP_FILL = "gap-fill"
    JUMP = "jump"
    LOCATION = "location"  # 地区 / 高校输入
    REORDER = "reorder"  # 排序题
    RATING = "rating"  # 星级评分
    SLIDER_MATRIX = "slider-matrix"
    CHOICE_INPUTS = "choice-inputs"  # radio / checkbox
    CONTROL_GROUP = "control-group"
    JQ_CONTROLS = "jq-controls"


_C_FIELD_LABEL = class_xpath("field-label")
_C_TOPICHTML = class_xpath("topichtml")
_C_TOPICNUMBER = class_xpath("topicnumber")
_C_QTYPE_TIP = class_xpath("qtypetip")

_XP_FIELD_LABEL = f".//*[{_C_FIELD_LABEL}]"
_XP_TOPICHTML = f".//*[{_C_TOPICHTML}]"
_XP_QTYPE_TIP = f".//*[{_C_QTYPE_TIP}]"
_XP_ANY_INPUT = ".//input"
_XP_TEXT_CANDIDATES = ".//input|.//textarea|.//span|.//div"

# 选项容器：控件组的直接子 div，或列表项（两者取其一，与旧实现一致）
_XP_OPTION_GROUPS = (f".//*[{class_xpath('ui-controlgroup')}]/div", ".//ul/li")

_XP_RATE_ICONS = f".//*[{in_class('rate-off', 'rate-on')}]"
_XP_ICONFONT = f".//*[{class_xpath('iconfontNew')}]"
_XP_SCALE_TITLES = f".//*[{in_class('scaleTitle', 'scaleTitle_frist', 'scaleTitle_last', 'scaleTitleFirst', 'scaleTitleLast')}]"
_XP_SCALE_ANCHORS = (
    ".//ul[@tp='d']/li/a"
    f"|.//*[{class_xpath('scale-rating')}]//ul/li/a"
    f"|.//*[{class_xpath('scale-rating')}]//a[@val]"
)
_XP_RATING_LISTS = f".//ul[{class_xpath('scale-rating')}]|.//a[{in_class('rate-off', 'rate-on')}]"
_XP_SLIDER_INPUTS = f".//input[{class_xpath('ui-slider-input')}][@rowid]"
_XP_SLIDER_TRACKS = f".//*[{in_class('rangeslider', 'range-slider', 'wjx-slider')}]"
_XP_SORT_MARKS = f".//*[{in_class('sortnum', 'sortnum-sel', 'order-number', 'order-index')}]"
_XP_SORT_SIGNATURE = (
    f".//*[{in_class('ui-sortable', 'ui-sortable-handle')}]|.//*[contains(@class,'sort')]"
)
_XP_REQUIRED_MARKS = (
    f".//*[{in_class('req', 'required', 'must', 'star', 'red', 'wjxreq')}]"
    "|.//*[@aria-required='true']"
)
# input type 可能是任意大小写，用 XPath 1.0 的 translate 归一化
_LOWER_TYPE = "translate(@type,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
_XP_CHOICE_INPUTS = f".//input[{_LOWER_TYPE}='radio' or {_LOWER_TYPE}='checkbox']"
_XP_CONTROL_GROUP = f".//*[{class_xpath('ui-controlgroup')}]"
_XP_JQ_CONTROLS = f".//*[{in_class('jqradio', 'jqcheck')}]"
_XP_BLOCKQUOTE = ".//blockquote"
_XP_MATRIX_ROWS = ".//tr[@rowindex]"
_XP_DRV_ROWS = ".//tr[starts-with(@id,'drv')]"

_DIGIT_RE = re.compile(r"\d{1,2}")
_UNREACHABLE_RELATION = "-1"
_LOCATION_VERIFY_MARKERS = ("地图", "省市", "省份", "城市", "地区", "高校")
_SELECT_PLACEHOLDER_PREFIXES = ("请选择", "请先选择")
_TRUE_VALUES = frozenset({"1", "true", "True"})
_TRUE_LOWER = frozenset({"1", "true", "required"})


@dataclass(slots=True)
class Features:
    """一道题从 DOM 中抽取出的全部原始信号。"""

    node: object
    root: object
    number: int | None = None
    raw_type_code: str = "0"
    heading_text: str = ""
    title_text: str = ""
    relation: str = ""
    style: str = ""
    location_verify: str = ""
    rating_max: int = 0
    markers: frozenset[Marker] = frozenset()
    text_input_count: int = 0
    text_input_labels: list[str] = field(default_factory=list)
    option_nodes: list = field(default_factory=list)
    matrix_table: object = None
    slider_inputs: list = field(default_factory=list)
    selectable_nodes: list = field(default_factory=list)


def extract(node, root) -> Features:
    """抽取单个题目 div 的全部特征。"""
    number = _question_number(node)
    heading = _heading_text(node)
    verifies = [
        value for value in (item.get("verify") or "" for item in nodes(node, _XP_ANY_INPUT))
    ]
    text_count, text_nodes = _collect_text_inputs(node)

    return Features(
        node=node,
        root=root,
        number=number,
        raw_type_code=node.get("type") or "0" if node is not None else "0",
        heading_text=heading,
        title_text=_title_text(node, number),
        relation=node.get("relation") or "" if node is not None else "",
        style=node.get("style") or "" if node is not None else "",
        location_verify=next((value for value in verifies if value), ""),
        rating_max=_rating_max(node),
        markers=_markers(node, verifies, heading),
        text_input_count=text_count,
        text_input_labels=_text_input_labels(text_nodes) if text_count > 1 else [],
        option_nodes=_option_nodes(node),
        matrix_table=_matrix_table(node, root, number),
        slider_inputs=nodes(node, _XP_SLIDER_INPUTS),
        selectable_nodes=_selectable_nodes(node),
    )


def _markers(node, verifies: list[str], heading_text: str) -> frozenset[Marker]:
    """把 DOM 信号一次性抽成标记集合。"""
    if node is None:
        return frozenset()
    candidates = (
        (Marker.HIDDEN, _hidden(node)),
        (Marker.REQUIRED, _required(node, heading_text)),
        (Marker.JUMP, (node.get("hasjump") or "") == "1"),
        (Marker.GAP_FILL, (node.get("gapfill") or "") == "1"),
        (Marker.LOCATION, _location(node, verifies)),
        (Marker.REORDER, _reorder(node)),
        (Marker.RATING, _rating(node)),
        (Marker.SLIDER_MATRIX, _slider_matrix(node)),
        (Marker.CHOICE_INPUTS, bool(nodes(node, _XP_CHOICE_INPUTS))),
        (Marker.CONTROL_GROUP, first(node, _XP_CONTROL_GROUP) is not None),
        (Marker.JQ_CONTROLS, first(node, _XP_JQ_CONTROLS) is not None),
    )
    return frozenset(marker for marker, present in candidates if present)


# --------------------------------------------------------------------------- 基础属性


def _question_number(node) -> int | None:
    topic = node.get("topic") or "" if node is not None else ""
    if topic.isdigit():
        return int(topic)
    match = re.search(r"div(\d+)", node.get("id") or "" if node is not None else "")
    return int(match.group(1)) if match else None


def _cleanup_title(raw: str) -> str:
    title = _normalize_html_text(raw)
    if not title:
        return ""
    title = WJX_QUESTION_PREFIX_RE.sub("", title)
    title = title.replace("【单选题】", "").replace("【多选题】", "")
    return title.strip()


def _title_text(node, fallback_number: int | None) -> str:
    element = first(node, _XP_TOPICHTML)
    if element is not None:
        text = _cleanup_title(text_of(element))
        if text:
            return text
    element = first(node, _XP_FIELD_LABEL)
    if element is not None:
        text = _cleanup_title(text_of(element))
        if text:
            return text
    return f"第{fallback_number}题"


def _heading_text(node) -> str:
    if node is None:
        return ""
    label = first(node, _XP_FIELD_LABEL)
    if label is not None:
        parts = []
        for predicate in (_C_TOPICNUMBER, _C_TOPICHTML):
            element = first(label, f".//*[{predicate}]")
            if element is None:
                continue
            text = _normalize_html_text(text_of(element))
            if text:
                parts.append(text)
        if parts:
            return _normalize_html_text(" ".join(parts))
    for predicate in (_C_TOPICHTML, _C_FIELD_LABEL, _C_QTYPE_TIP):
        element = first(node, f".//*[{predicate}]")
        if element is not None:
            text = _normalize_html_text(text_of(element))
            if text:
                return text
    quote = first(node, _XP_BLOCKQUOTE)
    if quote is not None:
        text = _normalize_html_text(text_of(quote))
        if text:
            return text
    return _normalize_html_text(text_of(node))


def _hidden(node) -> bool:
    """自身或任一祖先被隐藏。"""
    current = node
    while current is not None:
        if _node_hidden(current):
            return True
        current = current.getparent()
    return False


def _node_hidden(node) -> bool:
    if node is None:
        return False
    style = (node.get("style") or "").lower()
    hidden_attr = (node.get("hidden") or "").lower()
    return (
        "display:none" in style
        or "display: none" in style
        or "visibility:hidden" in style
        or "visibility: hidden" in style
        or hidden_attr in {"hidden", "true", "1"}
        or "display-none" in classes_of(node)
    )


def _required(node, heading_text: str) -> bool:
    if node is None:
        return False
    for attr in ("req", "must", "wjxreq"):
        if (node.get(attr) or "") in _TRUE_VALUES:
            return True
    if (node.get("required") or "").lower() in _TRUE_LOWER:
        return True
    if (node.get("aria-required") or "").lower() == "true":
        return True
    if first(node, _XP_REQUIRED_MARKS) is not None:
        return True
    if heading_text.startswith("*") or "必答" in heading_text:
        return True
    return _normalize_html_text(text_of(node)).startswith("*")


def _location(node, verifies: list[str]) -> bool:
    if node is None:
        return False
    if first(node, f".//*[{class_xpath('get_Local')}]") is not None:
        return True
    return any(_location_verify_hit(value) for value in verifies)


def _location_verify_hit(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return any(marker in text for marker in _LOCATION_VERIFY_MARKERS)


# --------------------------------------------------------------------------- 题型信号


def _reorder(node) -> bool:
    if node is None:
        return False
    if first(node, _XP_SORT_MARKS) is not None:
        return True
    if not nodes(node, ".//ul/li|.//ol/li"):
        return False
    return bool(nodes(node, _XP_SORT_SIGNATURE))


def _numeric_scale(node) -> bool:
    anchors = nodes(node, _XP_SCALE_ANCHORS)
    texts = []
    for anchor in anchors:
        text = _normalize_html_text(text_of(anchor))
        if not text:
            text = _normalize_html_text(
                anchor.get("title")
                or anchor.get("aria-label")
                or anchor.get("val")
                or anchor.get("value")
                or anchor.get("dval")
                or anchor.get("data-value")
                or anchor.get("data-val")
                or ""
            )
        if text:
            texts.append(text)
    if not texts:
        return False
    numeric = sum(1 for text in texts if _DIGIT_RE.fullmatch(text))
    total = len(texts)
    scale_titles = first(node, _XP_SCALE_TITLES)
    return (
        total >= 5
        and numeric >= max(3, int(total * 0.7))
        and (total >= 9 or scale_titles is not None)
    )


def _rating(node) -> bool:
    if node is None:
        return False
    if _numeric_scale(node):
        return False
    return (
        first(node, f".//*[{class_xpath('evaluateTagWrap')}]") is not None
        or first(node, _XP_RATE_ICONS) is not None
        or first(node, _XP_ICONFONT) is not None
    )


def _rating_max(node) -> int:
    if node is None:
        return 0
    for element in nodes(node, ".//ul"):
        for name in classes_of(element):
            match = WJX_MODLEN_CLASS_RE.search(name)
            if match:
                try:
                    return int(match.group("count"))
                except (ValueError, TypeError):
                    continue
    listed = nodes(node, f".//*[{class_xpath('scale-rating')}]//ul/li")
    if listed:
        return len(listed)
    return len(nodes(node, _XP_RATING_LISTS))


def _slider_matrix(node) -> bool:
    if node is None:
        return False
    inputs = nodes(node, _XP_SLIDER_INPUTS)
    if len(inputs) < 2:
        return False
    return len(nodes(node, _XP_SLIDER_TRACKS)) >= len(inputs)


# --------------------------------------------------------------------------- 节点集合


def _option_nodes(node) -> list:
    for xpath in _XP_OPTION_GROUPS:
        found = nodes(node, xpath)
        if found:
            return found
    return []


def _matrix_table(node, root, number: int | None):
    if number is None:
        return None
    xpath = f'.//*[@id="divRefTab{number}"]'
    table = first(node, xpath)
    return table if table is not None else first(root, f'//*[@id="divRefTab{number}"]')


def _selectable_nodes(node) -> list:
    """跳转规则的作用对象：radio/checkbox，没有时退化为下拉 option。"""
    found = nodes(node, _XP_CHOICE_INPUTS)
    if found:
        return found
    return [
        option
        for index, option in enumerate(nodes(node, ".//option"))
        if not placeholder_option(index, option.get("value"), text_of(option))
    ]


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


def _input_looks_like_location(element) -> bool:
    verify = element.get("verify") or ""
    onclick = (element.get("onclick") or "").lower()
    if not verify and "opencitybox" not in onclick:
        return False
    if any(marker in verify for marker in _LOCATION_VERIFY_MARKERS):
        return True
    return "opencitybox" in onclick


def _collect_text_inputs(node) -> tuple[int, list]:
    """一轮遍历同时得到可输入控件数量与节点列表。"""
    found: list = []
    for candidate in nodes(node, _XP_TEXT_CANDIDATES):
        tag = candidate.tag if isinstance(candidate.tag, str) else ""
        input_type = candidate.get("type")
        style = (candidate.get("style") or "").lower()
        class_text = " ".join(classes_of(candidate)).lower()
        editable_label = "textcont" in class_text or "textedit" in class_text
        if input_type == "hidden" or "display:none" in style or "visibility:hidden" in style:
            continue
        if tag == "input" and _input_looks_like_location(candidate):
            continue
        if tag == "input":
            sibling = candidate.getnext()
            if any("textedit" in name.lower() for name in classes_of(sibling)):
                continue
        if (
            tag == "textarea"
            or (tag == "input" and input_type in _TEXT_INPUT_TYPES)
            or (candidate.get("contenteditable") == "true" or editable_label)
            and tag in {"span", "div"}
        ):
            found.append(candidate)
    return len(found), found


def _previous_text(node) -> str | None:
    """等价于 BS4 ``find_previous_sibling(string=True)``。"""
    current = node.getprevious()
    while current is not None:
        if current.tail:
            return current.tail
        current = current.getprevious()
    parent = node.getparent()
    return parent.text if parent is not None else None


def _iter_previous(node):
    """从近到远产出 BS4 意义上的兄弟节点（文本片段与元素交替）。"""
    parent = node.getparent()
    if parent is None:
        return
    current = node.getprevious()
    while current is not None:
        if current.tail:
            yield ("text", current.tail)
        yield ("element", current)
        current = current.getprevious()
    if parent.text:
        yield ("text", parent.text)


def _label_before(node) -> str:
    parts: list[str] = []
    for kind, value in _iter_previous(node):
        if kind == "text":
            text = _normalize_html_text(value)
            if text:
                parts.append(text)
            continue
        tag = value.tag if isinstance(value.tag, str) else ""
        if tag in {"input", "textarea", "label", "span"}:
            if tag == "input":
                continue
            break
        if tag == "br":
            break
        text = text_of(value)
        if text:
            parts.append(text)
    return _normalize_html_text(" ".join(reversed(parts))).rstrip("：:").strip()


def _text_input_labels(text_nodes: list) -> list[str]:
    labels: list[str] = []
    for candidate in text_nodes:
        class_text = " ".join(classes_of(candidate)).lower()
        editable_label = "textcont" in class_text or "textedit" in class_text
        label = (
            candidate.get("placeholder")
            or candidate.get("aria-label")
            or candidate.get("data-label")
            or ""
        )
        if not label:
            previous = _previous_text(candidate)
            if previous:
                label = previous.strip().rstrip("：:").strip()
        if not label:
            label = _label_before(candidate)
        if not label and editable_label:
            parent = candidate.getparent()
            if parent is not None:
                label = _label_before(parent)
        labels.append(label if label else f"填空{len(labels) + 1}")
    return labels


__all__ = [
    "Features",
    "Marker",
    "_cleanup_title",
    "_heading_text",
    "_matrix_table",
    "_numeric_scale",
    "_option_nodes",
    "_question_number",
    "_rating",
    "_rating_max",
    "_reorder",
    "_slider_matrix",
    "_title_text",
    "extract",
    "placeholder_option",
]
