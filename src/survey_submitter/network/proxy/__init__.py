from __future__ import annotations

from survey_submitter.network.proxy.api import (
    ProxyApiFatalError,
    test_custom_proxy_api,
)
from survey_submitter.network.proxy.source import (
    ProxySettings,
    apply_custom_proxy_api,
    apply_proxy_area_code,
    apply_proxy_source_settings,
    get_custom_proxy_api_override,
    get_default_proxy_area_code,
    get_proxy_area_code,
    get_proxy_minute_by_answer_seconds,
    get_proxy_occupy_minute,
    get_proxy_ttl_for_answer_duration,
    get_proxy_settings,
    get_proxy_source,
    has_custom_proxy_api_override,
    is_custom_proxy_source,
    normalize_proxy_source,
    set_proxy_api_override,
    set_proxy_area_code,
    set_proxy_occupy_minute_by_answer_duration,
    set_proxy_source,
)
from survey_submitter.network.proxy.pool import (
    MIN_PROXY_TTL_SECONDS,
    get_proxy_required_ttl_seconds,
    proxy_lease_has_sufficient_ttl,
)
from survey_submitter.constants import PROXY_SOURCE_CUSTOM

__all__ = [
    "MIN_PROXY_TTL_SECONDS",
    "PROXY_SOURCE_CUSTOM",
    "ProxyApiFatalError",
    "ProxySettings",
    "apply_custom_proxy_api",
    "apply_proxy_area_code",
    "apply_proxy_source_settings",
    "get_custom_proxy_api_override",
    "get_default_proxy_area_code",
    "get_proxy_area_code",
    "get_proxy_minute_by_answer_seconds",
    "get_proxy_occupy_minute",
    "get_proxy_ttl_for_answer_duration",
    "get_proxy_required_ttl_seconds",
    "get_proxy_settings",
    "get_proxy_source",
    "has_custom_proxy_api_override",
    "is_custom_proxy_source",
    "normalize_proxy_source",
    "proxy_lease_has_sufficient_ttl",
    "set_proxy_api_override",
    "set_proxy_area_code",
    "set_proxy_occupy_minute_by_answer_duration",
    "set_proxy_source",
    "test_custom_proxy_api",
]
