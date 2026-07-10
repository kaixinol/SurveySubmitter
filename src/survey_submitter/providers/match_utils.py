from __future__ import annotations

import html
import re
import unicodedata
from typing import Any

_MATCH_SPACE_RE = re.compile(r"\s+")


def normalize_match_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        text = str(value)
    except Exception:
        return ""
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
        getter = getattr(element, "get_attribute", None)
        if callable(getter):
            try:
                value = getter(attr_name)
                if value is None:
                    value = _get_attribute_case_insensitive(element, attr_name, getter)
            except Exception:
                value = None
            if value is not None:
                return value
        getter = getattr(element, "get", None)
        if callable(getter):
            try:
                value = getter(attr_name)
                if value is None:
                    value = _get_dict_case_insensitive(element, attr_name)
            except Exception:
                value = None
            if value is not None:
                return value
        if isinstance(element, dict):
            value = element.get(attr_name)
            if value is None:
                value = _get_dict_case_insensitive(element, attr_name)
            if value is not None:
                return value
    return None


def _get_attribute_case_insensitive(element: Any, attr_name: str, getter) -> Any:
    attr_lower = attr_name.lower()
    for attr in dir(element):
        if attr.lower() == attr_lower:
            try:
                return getter(attr)
            except Exception:
                continue
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
