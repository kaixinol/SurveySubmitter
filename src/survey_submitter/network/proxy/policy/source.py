from __future__ import annotations

import logging
import re
import threading
from typing import Any

from pydantic import ConfigDict, field_validator

from survey_submitter.constants import (
    PROXY_MINUTE_OPTIONS,
    PROXY_POOL_QUALITY,
    PROXY_SOURCE_CUSTOM,
    PROXY_TTL_GRACE_SECONDS,
)
from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.providers.common import (
    SURVEY_PROVIDER_WJX,
)

_SUPPORTED_PROXY_SOURCES = frozenset({PROXY_SOURCE_CUSTOM})

_config_lock = threading.Lock()
_proxy_api_url_override: str | None = None
_proxy_area_code_override: str | None = None
_current_proxy_source: str = PROXY_SOURCE_CUSTOM
_proxy_occupy_minute: int = 1

_ORDINARY_POOL_PROVINCE_CODES: set[str] = {
    "110000",
    "120000",
    "130000",
    "140000",
    "150000",
    "210000",
    "220000",
    "230000",
    "320000",
    "330000",
    "340000",
    "350000",
    "360000",
    "370000",
    "410000",
    "420000",
    "430000",
    "440000",
    "460000",
    "500000",
    "510000",
    "610000",
    "620000",
    "640000",
}


def _safe_to_string(value: str | int | float | None, default: str = "") -> str:
    """Safely convert a value to string, returning default on exception."""
    if value is None:
        return default

    try:
        return str(value).strip()
    except (ValueError, TypeError):
        return default


class ProxySettings(BaseConfigModel):
    model_config = ConfigDict(frozen=True)
    source: str
    custom_api_url: str
    area_code: str | None
    default_area_code: str
    occupy_minute: int

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        if v not in _SUPPORTED_PROXY_SOURCES:
            raise ValueError(f"不支持的代理源: {v}")
        return v

    @field_validator("area_code", "default_area_code")
    @classmethod
    def validate_area_code(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^\d{6}$", v):
            raise ValueError(f"地区代码必须是6位数字: {v}")
        return v

    @field_validator("occupy_minute")
    @classmethod
    def validate_occupy_minute(cls, v: int) -> int:
        if v not in PROXY_MINUTE_OPTIONS:
            raise ValueError(f"占用分钟数必须是 {PROXY_MINUTE_OPTIONS} 之一")
        return v


def normalize_proxy_source(source: str | None) -> str:
    cleaned = _safe_to_string(source).lower()
    if cleaned in _SUPPORTED_PROXY_SOURCES:
        return cleaned
    return PROXY_SOURCE_CUSTOM


def set_proxy_source(source: str) -> None:
    global _current_proxy_source
    normalized = normalize_proxy_source(source)
    with _config_lock:
        _current_proxy_source = normalized
    logging.info(f"代理源已切换为: {normalized}")


def get_proxy_source() -> str:
    with _config_lock:
        return normalize_proxy_source(_current_proxy_source)


def is_custom_proxy_source(source: str | None = None) -> bool:
    current = get_proxy_source() if source is None else normalize_proxy_source(source)
    return current == PROXY_SOURCE_CUSTOM


def source_uses_custom_api_override(source: str | None = None) -> bool:
    return is_custom_proxy_source(source)


def _map_answer_seconds_to_proxy_minute(total_seconds: int) -> int:
    seconds = max(0, int(total_seconds))
    if seconds < 60:
        return 1
    if seconds <= 180:
        return 3
    if seconds <= 300:
        return 5
    if seconds <= 600:
        return 10
    if seconds <= 900:
        return 15
    return 30


def get_proxy_required_seconds_by_answer_seconds(total_seconds: int) -> int:
    return max(0, int(total_seconds)) + int(PROXY_TTL_GRACE_SECONDS)


def get_proxy_minute_by_answer_seconds(
    total_seconds: int,
    *,
    survey_provider: str | None = None,
) -> int:
    normalized_provider = str(survey_provider or "").strip().lower()
    if normalized_provider == SURVEY_PROVIDER_WJX:
        return 1
    required_seconds = get_proxy_required_seconds_by_answer_seconds(total_seconds)
    minute = int(_map_answer_seconds_to_proxy_minute(required_seconds))
    if minute not in PROXY_MINUTE_OPTIONS:
        return 1
    return minute


def set_proxy_occupy_minute_by_answer_duration(
    answer_duration_range_seconds: tuple[int, int] | None,
    *,
    survey_provider: str | None = None,
) -> int:
    global _proxy_occupy_minute
    min_seconds = max_seconds = 0
    if isinstance(answer_duration_range_seconds, (list, tuple)):
        if len(answer_duration_range_seconds) >= 1:
            first = answer_duration_range_seconds[0]
            if isinstance(first, (int, float, str)):
                try:
                    min_seconds = max(0, int(float(first)))
                except (ValueError, TypeError, OverflowError):
                    min_seconds = 0
        if len(answer_duration_range_seconds) >= 2:
            second = answer_duration_range_seconds[1]
            if isinstance(second, (int, float, str)):
                try:
                    max_seconds = max(min_seconds, int(float(second)))
                except (ValueError, TypeError, OverflowError):
                    max_seconds = min_seconds
            else:
                max_seconds = min_seconds
        else:
            max_seconds = min_seconds
    max_seconds = max(max_seconds, min_seconds)
    normalized_provider = str(survey_provider or "").strip().lower()
    minute = get_proxy_minute_by_answer_seconds(max_seconds, survey_provider=normalized_provider)
    required_seconds = get_proxy_required_seconds_by_answer_seconds(max_seconds)
    with _config_lock:
        _proxy_occupy_minute = minute
    if normalized_provider == SURVEY_PROVIDER_WJX:
        logging.info(
            "问卷星代理 minute 已固定为 %s（min=%s秒, max=%s秒）",
            minute,
            min_seconds,
            max_seconds,
        )
    else:
        logging.info(
            "已根据作答时长更新代理 minute=%s（provider=%s, min=%s秒, max=%s秒, ttl=%s秒）",
            minute,
            normalized_provider or "unknown",
            min_seconds,
            max_seconds,
            required_seconds,
        )
    return minute


def get_proxy_occupy_minute() -> int:
    with _config_lock:
        minute = int(_proxy_occupy_minute or 1)
    if minute not in PROXY_MINUTE_OPTIONS:
        return 1
    return minute


def _validate_proxy_api_url(api_url: str | None) -> str:
    cleaned = _safe_to_string(api_url)
    if not cleaned:
        return ""
    if not (cleaned.lower().startswith("http://") or cleaned.lower().startswith("https://")):
        raise ValueError("随机IP提取接口必须以 http:// 或 https:// 开头")
    return cleaned


def _normalize_area_code(area_code: str | None) -> str:
    cleaned = _safe_to_string(area_code)
    if not cleaned or not cleaned.isdigit() or len(cleaned) != 6:
        return ""
    return cleaned


def _is_province_level_area_code(area_code: str) -> bool:
    return (
        bool(area_code)
        and len(area_code) == 6
        and area_code.isdigit()
        and area_code.endswith("0000")
    )


def _resolve_default_pool_by_area(area_code: str | None) -> str | None:
    normalized_area = _normalize_area_code(area_code)
    if not normalized_area:
        return None
    return PROXY_POOL_QUALITY


def get_default_proxy_area_code() -> str:
    with _config_lock:
        return _normalize_area_code(_proxy_area_code_override) or ""


def get_effective_proxy_api_url() -> str:
    with _config_lock:
        return (_proxy_api_url_override or "").strip()


def get_custom_proxy_api_override() -> str:
    with _config_lock:
        return (_proxy_api_url_override or "").strip()


def has_custom_proxy_api_override() -> bool:
    return bool(get_custom_proxy_api_override())


def is_custom_proxy_api_active() -> bool:
    return is_custom_proxy_source()


def get_proxy_area_code() -> str | None:
    with _config_lock:
        return _proxy_area_code_override


def set_proxy_area_code(area_code: str | None) -> str | None:
    global _proxy_area_code_override
    with _config_lock:
        if area_code is None:
            _proxy_area_code_override = None
            return None
        _proxy_area_code_override = _normalize_area_code(area_code)
        return _proxy_area_code_override


def set_proxy_api_override(api_url: str | None) -> str:
    global _proxy_api_url_override
    cleaned = _validate_proxy_api_url(api_url)
    with _config_lock:
        _proxy_api_url_override = cleaned or None
    return get_effective_proxy_api_url()


def get_proxy_settings() -> ProxySettings:

    return ProxySettings(
        source=normalize_proxy_source(get_proxy_source()),
        custom_api_url=get_custom_proxy_api_override(),
        area_code=get_proxy_area_code(),
        default_area_code=get_default_proxy_area_code(),
        occupy_minute=int(get_proxy_occupy_minute() or 1),
    )


def apply_proxy_source_settings(source: str, *, custom_api_url: str | None = None) -> ProxySettings:

    normalized = normalize_proxy_source(source)
    if normalized == PROXY_SOURCE_CUSTOM:
        set_proxy_api_override(custom_api_url if custom_api_url else None)
    else:
        set_proxy_api_override(None)
    set_proxy_source(normalized)
    return get_proxy_settings()


def apply_proxy_area_code(area_code: str | None) -> ProxySettings:

    set_proxy_area_code(area_code)
    return get_proxy_settings()


def apply_custom_proxy_api(custom_api_url: str | None) -> ProxySettings:

    set_proxy_api_override(custom_api_url if custom_api_url else None)
    return get_proxy_settings()
