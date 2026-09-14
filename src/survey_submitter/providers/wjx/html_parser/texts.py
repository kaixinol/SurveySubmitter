"""文本与节点基础工具。

用 lxml 复刻旧实现依赖的 BS4 语义：``get_text(" ", strip=True)``、class 列表、
按优先级取属性值。上层只通过 XPath 取节点，本模块负责把节点变成干净文本。
"""

from __future__ import annotations

import html as html_lib
import re

_DISPLAY_SPACE_RE = re.compile(r"\s+")
_MEANINGFUL_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")

# 会被计为“可输入文本”的 input type（旧 common 实现）
_TEXT_INPUT_TYPES = frozenset({"text", "tel", "email", "number", "search", "url", "password"})
# 判断元素是否为文本输入控件（旧 choice 实现）
_TEXT_ELEMENT_TYPES = frozenset({"", "text", "search", "tel", "number"})

_PRIMARY_TEXT_KEYS = (
    "title",
    "data-title",
    "data-text",
    "data-label",
    "aria-label",
    "alt",
    "htitle",
)
_VALUE_TEXT_KEYS = ("val", "value", "data-value", "data-val")

_SELECT_PLACEHOLDER_PREFIXES = ("请选择", "请先选择")


def looks_like_placeholder(text: str | None) -> bool:
    """只看文本前缀的“占位选项”判定（拿不到选项值时使用）。"""
    compact = _normalize_html_text(text).replace(" ", "")
    return any(compact.startswith(prefix) for prefix in _SELECT_PLACEHOLDER_PREFIXES)


def desc_or_self(step: str) -> str:
    """还原 CSS 子孙选择器的语义：节点自身也可作为祖先命中。

    BS4 的 ``node.select(".ui-other input")`` 在 node 本身就是 ``.ui-other``
    时同样命中；XPath 的 ``.//`` 只看子孙，需要用 ``descendant-or-self`` 对齐。
    """
    return f"descendant-or-self::{step}"


def _normalize_html_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    text = _DISPLAY_SPACE_RE.sub(" ", html_lib.unescape(text))
    return text.strip()


def text_of(node) -> str:
    """等价于 BS4 ``node.get_text(" ", strip=True)``。"""
    if node is None:
        return ""
    return " ".join(part for part in (chunk.strip() for chunk in node.itertext()) if part)


def classes_of(node) -> list[str]:
    if node is None:
        return []
    return [str(name) for name in str(node.get("class") or "").split()]


def has_class(node, name: str) -> bool:
    return name in classes_of(node)


def text_input_control(node) -> bool:
    if node is None:
        return False
    tag = node.tag if isinstance(node.tag, str) else ""
    if tag == "textarea":
        return True
    return tag == "input" and (node.get("type") or "").lower() in _TEXT_ELEMENT_TYPES


def _first_attr(node, keys: tuple[str, ...]) -> str:
    if node is None:
        return ""
    for key in keys:
        raw = node.get(key)
        if raw is None:
            continue
        text = _normalize_html_text(raw)
        if text:
            return text
    return ""


def attr_text(node, extra_keys: tuple[str, ...] = ()) -> str:
    """矩阵/行标题场景：只在节点自身按优先级取属性文本。"""
    return _first_attr(node, _PRIMARY_TEXT_KEYS + extra_keys)


def option_text_from_attrs(node) -> str:
    """选项场景：自身属性 → 前 4 个子节点属性 → 取值类属性。"""
    text = _first_attr(node, _PRIMARY_TEXT_KEYS)
    if text:
        return text
    children = nodes(node, ".//a | .//span | .//label")[:4]
    for child in children:
        text = _first_attr(child, _PRIMARY_TEXT_KEYS)
        if text:
            return text
    for target in (node, *children):
        text = _first_attr(target, _VALUE_TEXT_KEYS)
        if text:
            return text
    return ""


def has_content(text: str | None) -> bool:
    return bool(text) and bool(_MEANINGFUL_RE.search(text))


def nodes(node, xpath: str) -> list:
    """对节点求 XPath；节点为空时返回空列表。"""
    if node is None:
        return []
    return node.xpath(xpath)


def first(node, xpath: str):
    """取 XPath 命中的第一个节点（文档顺序，等价于 BS4 的 find）。"""
    found = nodes(node, xpath)
    return found[0] if found else None


def class_xpath(name: str) -> str:
    """生成严格匹配单个 class 名的 XPath 谓词。"""
    return f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'


def in_class(*names: str) -> str:
    """生成“class 命中任意一个”的 XPath 谓词。"""
    return " or ".join(class_xpath(name) for name in names)


__all__ = [
    "_TEXT_INPUT_TYPES",
    "_normalize_html_text",
    "attr_text",
    "class_xpath",
    "classes_of",
    "desc_or_self",
    "first",
    "has_class",
    "has_content",
    "in_class",
    "looks_like_placeholder",
    "nodes",
    "option_text_from_attrs",
    "text_input_control",
    "text_of",
]
