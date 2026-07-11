from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse, urlsplit, urlunsplit

SURVEY_PROVIDER_WJX = "wjx"
SUPPORTED_SURVEY_PROVIDERS = {SURVEY_PROVIDER_WJX}

_WJX_ALLOWED_HOSTS = ("wjx.top", "wjx.cn", "wjx.com")
_WJX_SURVEY_HOSTS = ("v.wjx.cn", "www.wjx.cn", "www.wjx.top")


def normalize_survey_provider(value: Any, default: str = SURVEY_PROVIDER_WJX) -> str:
    try:
        provider = str(value or "").strip().lower()
    except (ValueError, TypeError):
        provider = ""
    return provider if provider in SUPPORTED_SURVEY_PROVIDERS else str(default or SURVEY_PROVIDER_WJX)


def _parse_url_host(url_value: str) -> tuple[str, str]:
    text = str(url_value or "").strip()
    if not text:
        return "", ""
    candidate = text if "://" in text else f"https://{text}"
    try:
        parsed = urlparse(candidate)
    except (ValueError, TypeError):
        return "", ""
    host = (parsed.netloc or "").split(":", 1)[0].lower()
    path = str(parsed.path or "").strip()
    return host, path


def is_wjx_domain(url_value: str) -> bool:
    host, _ = _parse_url_host(url_value)
    if not host:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in _WJX_ALLOWED_HOSTS)


def is_wjx_survey_url(url_value: str) -> bool:
    host, _ = _parse_url_host(url_value)
    if not host:
        return False
    return host in _WJX_SURVEY_HOSTS or host.endswith(".v.wjx.cn")


def detect_survey_provider(url_value: str, default: str = SURVEY_PROVIDER_WJX) -> str:
    if is_wjx_domain(url_value):
        return SURVEY_PROVIDER_WJX
    return normalize_survey_provider(default)


def supports_answer_datetime_window(provider: Any) -> bool:
    return False


def is_supported_survey_url(url_value: str) -> bool:
    return is_wjx_domain(url_value)


def normalize_survey_parse_url(url_value: str) -> str:
    text = str(url_value or "").strip()
    if not text:
        return ""
    candidate = text if "://" in text else f"https://{text}"
    try:
        parsed = urlsplit(candidate)
    except (ValueError, TypeError):
        return text
    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    path = str(parsed.path or "")
    fragment = str(parsed.fragment or "")
    normalized = urlunsplit((scheme, netloc, path, parsed.query or "", fragment))
    return normalized


def ensure_question_provider_fields(
    item: dict[str, Any],
    *,
    default_provider: str = SURVEY_PROVIDER_WJX,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    normalized = dict(item)
    provider = normalize_survey_provider(normalized.get("provider"), default=default_provider)
    normalized["provider"] = provider
    normalized["provider_question_id"] = str(normalized.get("provider_question_id") or "").strip()
    normalized["provider_page_id"] = str(normalized.get("provider_page_id") or "").strip()
    normalized["provider_type"] = str(normalized.get("provider_type") or "").strip()
    normalized["provider_page_raw"] = normalized.get("provider_page_raw")
    normalized["unsupported"] = bool(normalized.get("unsupported", False))
    normalized["unsupported_reason"] = str(normalized.get("unsupported_reason") or "").strip()
    return normalized


def ensure_questions_provider_fields(
    items: Iterable[dict[str, Any]],
    *,
    default_provider: str = SURVEY_PROVIDER_WJX,
) -> list[dict[str, Any]]:
    normalized_items: list[dict[str, Any]] = []
    for item in items or []:
        normalized = ensure_question_provider_fields(item, default_provider=default_provider)
        if normalized:
            normalized_items.append(normalized)
    return normalized_items


def make_provider_question_key(
    provider: Any,
    provider_page_id: Any,
    provider_question_id: Any,
) -> str:
    normalized_provider = normalize_survey_provider(provider, default=SURVEY_PROVIDER_WJX)
    page_id = str(provider_page_id or "").strip()
    question_id = str(provider_question_id or "").strip()
    if not page_id or not question_id:
        return ""
    return f"{normalized_provider}:{page_id}:{question_id}"


__all__ = [
    "SURVEY_PROVIDER_WJX",
    "SUPPORTED_SURVEY_PROVIDERS",
    "normalize_survey_provider",
    "is_wjx_domain",
    "is_wjx_survey_url",
    "detect_survey_provider",
    "supports_answer_datetime_window",
    "is_supported_survey_url",
    "normalize_survey_parse_url",
    "ensure_question_provider_fields",
    "ensure_questions_provider_fields",
    "make_provider_question_key",
]
