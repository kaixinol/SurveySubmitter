"""Helpers for loading and sampling location/university data for WJX location questions."""

from __future__ import annotations

import json
import os
import random
from typing import Any, Optional

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "assets")

_LOCATION_TREE: Optional[list[dict[str, Any]]] = None
_UNIVERSITY_LIST: Optional[list[list[str]]] = None

# Suffixes to strip from province full names to get short names,
# ordered longest-first to avoid partial matches.
_PROVINCE_SHORT_NAME_SUFFIXES = (
    "维吾尔自治区",
    "壮族自治区",
    "回族自治区",
    "特别行政区",
    "自治区",
    "省",
    "市",
)

# The four municipalities whose children are districts directly (no city level).
_MUNICIPALITY_NAMES = frozenset({"北京市", "天津市", "上海市", "重庆市"})


def _province_short_name(full_name: str) -> str:
    """Convert a full province name to its short display form.

    Examples:
        北京市 -> 北京
        河北省 -> 河北
        内蒙古自治区 -> 内蒙古
        广西壮族自治区 -> 广西
        香港特别行政区 -> 香港
    """
    for suffix in _PROVINCE_SHORT_NAME_SUFFIXES:
        if full_name.endswith(suffix):
            return full_name[: -len(suffix)]
    return full_name


def _load_location_tree() -> list[dict[str, Any]]:
    global _LOCATION_TREE
    if _LOCATION_TREE is not None:
        return _LOCATION_TREE
    path = os.path.join(_ASSETS_DIR, "location_tree_2022.json")
    with open(path, encoding="utf-8") as fp:
        _LOCATION_TREE = json.load(fp)
    return _LOCATION_TREE


def _load_university_list() -> list[list[str]]:
    global _UNIVERSITY_LIST
    if _UNIVERSITY_LIST is not None:
        return _UNIVERSITY_LIST
    path = os.path.join(_ASSETS_DIR, "university_list.json")
    with open(path, encoding="utf-8") as fp:
        _UNIVERSITY_LIST = json.load(fp)
    return _UNIVERSITY_LIST


def sample_location_text() -> str:
    """Randomly select a valid province-city-district path and return the WJX submit format.

    Format: ``省-市-区`` where:
    - Province uses SHORT name (e.g. 北京, 河北, 内蒙古)
    - City uses FULL name from data (e.g. 北京市, 石家庄市)
    - For municipalities: city name = short_name + 市 (e.g. 北京 -> 北京市)
    - District uses FULL name from data (e.g. 海淀区, 长安区)

    Returns a string like ``北京-北京市-海淀区`` or ``河北-石家庄市-长安区``.
    """
    tree = _load_location_tree()
    # Filter out provinces with no children (e.g. 台湾省)
    provinces_with_data = [p for p in tree if p.get("children")]
    if not provinces_with_data:
        return "北京-北京市-海淀区"

    province = random.choice(provinces_with_data)
    province_full_name = province["name"]
    province_short = _province_short_name(province_full_name)
    children = province["children"]

    if not children:
        return f"{province_short}-{province_full_name}-未知区"

    # Check if this is a municipality (children are districts directly, no city level)
    first_child = children[0]
    is_municipality = province_full_name in _MUNICIPALITY_NAMES or "children" not in first_child

    if is_municipality:
        # Municipality: city name = short name + 市
        city_name = f"{province_short}市"
        district = random.choice(children)
        district_name = district["name"]
        return f"{province_short}-{city_name}-{district_name}"
    else:
        # Regular province: children are cities with sub-children districts
        cities_with_data = [c for c in children if c.get("children")]
        if not cities_with_data:
            # Fallback: treat children as districts
            city_name = children[0].get("name", f"{province_short}市")
            district = random.choice(children)
            district_name = district["name"]
            return f"{province_short}-{city_name}-{district_name}"

        city = random.choice(cities_with_data)
        city_name = city["name"]
        district = random.choice(city["children"])
        district_name = district["name"]
        return f"{province_short}-{city_name}-{district_name}"


def sample_university_text() -> str:
    """Randomly select a university name from the list.

    Returns a string like ``北京大学``.
    """
    universities = _load_university_list()
    if not universities:
        return "北京大学"
    entry = random.choice(universities)
    # Each entry is [province, university_name]
    return entry[1] if len(entry) >= 2 else str(entry[0])


def is_university_verify(verify_type: str) -> bool:
    """Check if the verify type indicates a university (高校) question."""
    return "高校" in str(verify_type or "")
