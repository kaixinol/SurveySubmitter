from __future__ import annotations

import json
import re
import threading
from importlib import resources
from typing import Any, cast

from loguru import logger

_AREA_CODE_PATTERN = re.compile(r"^\d{6}$")

_MUNICIPALITY_REQUEST_NAMES = {
    "110000": "北京",
    "120000": "天津",
    "310000": "上海",
    "500000": "重庆",
}
_PROVINCE_SUFFIXES = (
    "维吾尔自治区",
    "回族自治区",
    "壮族自治区",
    "特别行政区",
    "自治区",
    "省",
    "市",
)
_CITY_SUFFIXES = (
    "特别行政区",
    "自治州",
    "地区",
    "盟",
    "市",
)


def _read_asset_text(filename: str) -> str:
    try:
        asset_file = resources.files("survey_submitter.assets").joinpath(filename)
        return asset_file.read_text(encoding="utf-8")
    except OSError:
        return ""


_AREA_CODES_CACHE: dict[str, object] | None = None
_SUPPORTED_CODES_CACHE: tuple[set[str], bool] | None = None
_SUPPORTED_AREA_CACHE_LOCK = threading.RLock()
_SUPPORTED_AREA_LIST_CACHE: list[dict[str, object]] | None = None
_SUPPORTED_AREA_CITY_CODE_INDEX_CACHE: dict[str, str] | None = None


def _normalize_province_name(name: str | None) -> str:
    text = re.sub(r"\s+", "", (name or ""))
    for suffix in _PROVINCE_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text


def _normalize_city_name(name: str | None) -> str:
    text = re.sub(r"\s+", "", (name or ""))
    if text == "市辖区":
        return text
    for suffix in _CITY_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text


def _normalize_area_code(area_code: str | None) -> str:
    text = area_code or ""
    return text if _AREA_CODE_PATTERN.fullmatch(text) else ""


def load_supported_area_codes() -> tuple[set[str], bool]:

    global _SUPPORTED_CODES_CACHE

    if _SUPPORTED_CODES_CACHE is not None:
        return _SUPPORTED_CODES_CACHE

    codes: set[str] = set()
    has_all = False
    content = _read_asset_text("area.txt")
    if not content:
        _SUPPORTED_CODES_CACHE = (codes, has_all)
        return codes, has_all

    for raw_line in content.splitlines():
        line = raw_line
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        code = str(parts[-1])
        if not code:
            continue
        if code.lower() == "all":
            has_all = True
            continue
        if _AREA_CODE_PATTERN.fullmatch(code):
            codes.add(code)

    _SUPPORTED_CODES_CACHE = (codes, has_all)
    return codes, has_all


def load_area_codes(supported_only: bool = False) -> list[dict[str, object]]:

    global _AREA_CODES_CACHE

    if _AREA_CODES_CACHE is None:
        try:
            _AREA_CODES_CACHE = json.loads(_read_asset_text("area_codes_2022.json") or "{}")
        except json.JSONDecodeError:
            _AREA_CODES_CACHE = {}

    area_codes_cache: dict[str, object] = (
        _AREA_CODES_CACHE if isinstance(_AREA_CODES_CACHE, dict) else {}
    )
    provinces = area_codes_cache.get("provinces")
    if not isinstance(provinces, list):
        return []
    if not supported_only:
        return cast("list[dict[str, object]]", provinces)

    supported_codes, _ = load_supported_area_codes()
    if not supported_codes:
        return []

    filtered: list[dict[str, object]] = []
    for province in provinces:
        if not isinstance(province, dict):
            continue
        province_typed = cast("dict[str, Any]", province)
        province_code = str(province_typed.get("code") or "")
        cities = province_typed.get("cities") or []
        if not isinstance(cities, list):
            cities = []
        supported_cities = [
            city
            for city in cities
            if isinstance(city, dict) and str(city.get("code") or "") in supported_codes
        ]
        if province_code not in supported_codes and not supported_cities:
            continue
        filtered.append({**province_typed, "cities": supported_cities})
    return filtered


def _build_local_area_lookup() -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    provinces = load_area_codes(supported_only=False)
    province_lookup: dict[str, dict[str, object]] = {}

    for province in provinces:
        if not isinstance(province, dict):
            continue
        province_code = _normalize_area_code(cast("str | None", province.get("code")))
        province_name = str(province.get("name") or "")
        if not province_code or not province_name:
            continue
        province_request_name = _MUNICIPALITY_REQUEST_NAMES.get(
            province_code, _normalize_province_name(province_name)
        )
        cities = province.get("cities") or []
        if not isinstance(cities, list):
            cities = []
        city_entries: list[dict[str, object]] = []
        for city in cities:
            if not isinstance(city, dict):
                continue
            city_code = _normalize_area_code(cast("str | None", city.get("code")))
            city_name = str(city.get("name") or "")
            if not city_code or not city_name:
                continue
            request_name = (
                province_request_name
                if city_name == "市辖区" and province_code in _MUNICIPALITY_REQUEST_NAMES
                else _normalize_city_name(city_name)
            )
            city_entries.append(
                {
                    "code": city_code,
                    "name": city_name,
                    "normalized_name": _normalize_city_name(request_name),
                    "request_name": request_name,
                    "raw": city,
                }
            )
        province_lookup[_normalize_province_name(province_name)] = {
            "code": province_code,
            "name": province_name,
            "request_name": province_request_name,
            "raw": province,
            "cities": city_entries,
        }

    return provinces, province_lookup


def _build_local_supported_area_index() -> tuple[list[dict[str, object]], dict[str, str]]:
    supported_codes, _ = load_supported_area_codes()
    _, province_lookup = _build_local_area_lookup()
    filtered_provinces: list[dict[str, object]] = []
    city_code_index: dict[str, str] = {}

    for province in province_lookup.values():
        matched_cities: list[dict[str, Any]] = []
        for city_entry in cast("list[dict[str, Any]]", province["cities"]):
            city_code = str(city_entry.get("code") or "")
            if city_code not in supported_codes:
                continue
            matched_cities.append(dict(city_entry["raw"]))
            city_code_index[city_code] = str(city_entry["request_name"])
        if matched_cities:
            filtered_provinces.append(
                {**cast("dict[str, Any]", province["raw"]), "cities": matched_cities}
            )

    return filtered_provinces, city_code_index


def _ensure_supported_area_cache(force_refresh: bool = False) -> None:
    global _SUPPORTED_AREA_LIST_CACHE, _SUPPORTED_AREA_CITY_CODE_INDEX_CACHE

    with _SUPPORTED_AREA_CACHE_LOCK:
        if (
            not force_refresh
            and _SUPPORTED_AREA_LIST_CACHE is not None
            and _SUPPORTED_AREA_CITY_CODE_INDEX_CACHE is not None
        ):
            return
        supported_areas, city_code_index = _build_local_supported_area_index()
        logger.info(
            f"地区支持列表已从本地数据刷新：省份={len(supported_areas)} 城市={len(city_code_index)}"
        )
        _SUPPORTED_AREA_LIST_CACHE = supported_areas
        _SUPPORTED_AREA_CITY_CODE_INDEX_CACHE = city_code_index


def build_supported_area_city_code_index(force_refresh: bool = False) -> dict[str, str]:

    _ensure_supported_area_cache(force_refresh=force_refresh)
    with _SUPPORTED_AREA_CACHE_LOCK:
        return dict(_SUPPORTED_AREA_CITY_CODE_INDEX_CACHE or {})


def resolve_proxy_area_for_source(source: str, area_code: str | None) -> str:

    normalized_code = _normalize_area_code(area_code)
    if not normalized_code:
        return ""
    source_key = (source or "").lower()
    if source_key == "supported_area":
        return str(build_supported_area_city_code_index().get(normalized_code) or "")
    return normalized_code
