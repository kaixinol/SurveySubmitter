from __future__ import annotations

import html
import re
import unicodedata
from typing import Any

_MATCH_SPACE_RE = re.compile(r"\s+")


def normalize_match_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = _MATCH_SPACE_RE.sub(" ", text)
    return text.strip()


def get_element_attribute(element: Any, *names: str) -> Any:
    if element is None:
        return None
    for name in names:
        attr_name = str(name or "").strip()
        if not attr_name:
            continue
        if isinstance(element, dict):
            value = element.get(attr_name)
            if value is None:
                value = _get_dict_case_insensitive(element, attr_name)
            if value is not None:
                return value
        else:
            value = element.get(attr_name)
            if value is None:
                value = _get_attribute_case_insensitive(element, attr_name)
            if value is not None:
                return value
    return None


def _get_attribute_case_insensitive(element: Any, attr_name: str) -> Any:
    attr_lower = attr_name.lower()
    attrs = getattr(element, "attrs", None)
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            if str(k).lower() == attr_lower:
                return v
    return None


def _get_dict_case_insensitive(element: Any, key: str) -> Any:
    if not isinstance(element, dict):
        return None
    key_lower = key.lower()
    for k, v in element.items():
        if str(k).lower() == key_lower:
            return v
    return None


__all__ = ["get_element_attribute", "normalize_match_text"]
