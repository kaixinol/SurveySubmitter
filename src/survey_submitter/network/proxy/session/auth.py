from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import Any, Dict, List, Optional

from survey_submitter.io.config.settings_store import app_settings
from survey_submitter.logging.log_utils import log_suppressed_exception
from survey_submitter.system.device_fingerprint import build_stable_device_id
from survey_submitter.system.secure_store import read_secret, set_secret

from .models import RandomIPAuthError, RandomIPSession
from .normalize import (
    _build_quota_snapshot,
    _is_valid_user_id,
    _normalize_quota_known,
    _normalize_quota_state,
    _quota_equals,
    _session_log_fields,
    _session_state_name,
    _to_non_negative_int,
    _to_non_negative_quota,
    _to_optional_bool,
    format_quota_value,
)

_SESSION_PREFIX = "random_ip_auth/"

_DEVICE_SECRET_KEY = "random_ip/device_id"

_SESSION_PERSIST_FAILED_DETAIL = "session_persist_failed"
_EXTRACT_REQUEST_BASE_TIMEOUT_SECONDS = 10.0
_EXTRACT_REQUEST_EXTRA_TIMEOUT_PER_PROXY_SECONDS = 2.0
_EXTRACT_REQUEST_MAX_TIMEOUT_SECONDS = 60.0

_session_lock = threading.RLock()

_session_loaded = False

_session = RandomIPSession()


def _get_settings() -> Any:
    return app_settings()

def _settings_key(name: str) -> str:
    return f"{_SESSION_PREFIX}{name}"

def _has_complete_session(session: RandomIPSession) -> bool:
    return _is_valid_user_id(session.user_id)

def _log_session_event(level: int, message: str, session: Optional[RandomIPSession] = None, **fields: Any) -> None:
    parts = [message]
    if session is not None:
        parts.append(_session_log_fields(session))
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    logging.log(level, "\u968f\u673aIP\u4f1a\u8bdd\uff1a%s", " | ".join(parts))

def _endpoint_name(url: str) -> str:
    from urllib.parse import urlsplit
    parsed = urlsplit(str(url or ""))
    host = str(parsed.netloc or "").strip() or "-"
    path = str(parsed.path or "").strip() or "/"
    return f"{host}{path}"

def _extract_request_timeout_seconds(num: int) -> float:
    
    request_num = max(1, _to_non_negative_int(num, 1))
    timeout = _EXTRACT_REQUEST_BASE_TIMEOUT_SECONDS + (
        (request_num - 1) * _EXTRACT_REQUEST_EXTRA_TIMEOUT_PER_PROXY_SECONDS
    )
    return min(_EXTRACT_REQUEST_MAX_TIMEOUT_SECONDS, timeout)

def _ensure_loaded() -> None:
    global _session_loaded, _session
    with _session_lock:
        if _session_loaded:
            return
        settings = _get_settings()
        device_secret = read_secret(_DEVICE_SECRET_KEY)
        device_id = device_secret.value.strip()
        device_from = "secure_store"
        if not device_id:
            device_id = str(settings.value(_settings_key("device_id")) or "").strip()
            device_from = "settings" if device_id else "generated"
        if not device_id:
            device_id = build_stable_device_id()
            set_secret(_DEVICE_SECRET_KEY, device_id)
        loaded_user_id = _to_non_negative_int(settings.value(_settings_key("user_id")), 0)
        loaded_remaining_quota = _to_non_negative_quota(settings.value(_settings_key("remaining_quota")), 0.0)
        loaded_total_quota = _to_non_negative_quota(settings.value(_settings_key("total_quota")), loaded_remaining_quota)
        loaded_used_quota = _to_non_negative_quota(
            settings.value(_settings_key("used_quota")),
            max(0.0, loaded_total_quota - loaded_remaining_quota),
        )
        loaded_quota_known = _to_optional_bool(settings.value(_settings_key("quota_known")))
        normalized_remaining, normalized_total, normalized_used = _normalize_quota_state(
            remaining_quota=loaded_remaining_quota,
            total_quota=loaded_total_quota,
            used_quota=loaded_used_quota,
        )
        loaded_session = RandomIPSession(
            device_id=device_id,
            user_id=loaded_user_id,
            remaining_quota=normalized_remaining,
            total_quota=normalized_total,
            used_quota=normalized_used,
            quota_known=_normalize_quota_known(
                user_id=loaded_user_id,
                total_quota=normalized_total,
                used_quota=normalized_used,
                quota_known=loaded_quota_known,
            ),
        )
        _session = loaded_session
        _session_loaded = True
        log_level = logging.INFO
        if device_secret.status not in {"ok", "not_found"}:
            log_level = logging.WARNING
        _log_session_event(
            log_level,
            "\u542f\u52a8\u52a0\u8f7d\u5b8c\u6210",
            loaded_session,
            device_secret=device_secret.status,
            device_from=device_from,
            settings_user_id=loaded_user_id,
        )

def _persist_session_locked() -> None:
    settings = _get_settings()
    settings.setValue(_settings_key("device_id"), str(_session.device_id or "").strip())
    settings.setValue(_settings_key("user_id"), int(_session.user_id or 0))
    settings.setValue(_settings_key("remaining_quota"), format_quota_value(_session.remaining_quota))
    settings.setValue(_settings_key("total_quota"), format_quota_value(_session.total_quota))
    settings.setValue(_settings_key("used_quota"), format_quota_value(_session.used_quota))
    settings.setValue(_settings_key("quota_known"), bool(_session.quota_known))
    settings.sync()
    set_secret(_DEVICE_SECRET_KEY, _session.device_id)

def _verify_persisted_session(session: RandomIPSession) -> None:
    settings = _get_settings()
    failures: List[str] = []
    expected_device_id = str(session.device_id or "").strip()
    expected_user_id = int(session.user_id or 0)
    expected_remaining_quota = _to_non_negative_quota(session.remaining_quota, 0.0)
    expected_total_quota = _to_non_negative_quota(session.total_quota, 0.0)
    expected_used_quota = _to_non_negative_quota(session.used_quota, 0.0)
    expected_quota_known = bool(session.quota_known)

    persisted_device_id = str(settings.value(_settings_key("device_id")) or "").strip()
    if persisted_device_id != expected_device_id:
        failures.append("settings.device_id")

    persisted_user_id = _to_non_negative_int(settings.value(_settings_key("user_id")), -1)
    if persisted_user_id != expected_user_id:
        failures.append("settings.user_id")

    persisted_remaining_quota = _to_non_negative_quota(settings.value(_settings_key("remaining_quota")), -1.0)
    if not _quota_equals(persisted_remaining_quota, expected_remaining_quota):
        failures.append("settings.remaining_quota")

    persisted_total_quota = _to_non_negative_quota(settings.value(_settings_key("total_quota")), -1.0)
    if not _quota_equals(persisted_total_quota, expected_total_quota):
        failures.append("settings.total_quota")

    persisted_used_quota = _to_non_negative_quota(settings.value(_settings_key("used_quota")), -1.0)
    if not _quota_equals(persisted_used_quota, expected_used_quota):
        failures.append("settings.used_quota")

    persisted_quota_known = _to_optional_bool(settings.value(_settings_key("quota_known")))
    if persisted_quota_known is None or bool(persisted_quota_known) != expected_quota_known:
        failures.append("settings.quota_known")

    persisted_device_secret = read_secret(_DEVICE_SECRET_KEY)
    if persisted_device_secret.status == "unsupported":
        _log_session_event(
            logging.WARNING,
            "\u5b89\u5168\u5b58\u50a8\u4e0d\u53ef\u7528\uff0c\u8bbe\u5907\u6807\u8bc6\u6301\u4e45\u5316\u6821\u9a8c\u964d\u7ea7\u4e3a\u4ec5\u68c0\u67e5\u672c\u5730\u8bbe\u7f6e",
            session,
            device_secret=persisted_device_secret.status,
        )
    elif persisted_device_secret.value.strip() != expected_device_id:
        failures.append(f"secure_store.device_id[{persisted_device_secret.status}]")

    if failures:
        logging.error("\u968f\u673aIP\u4f1a\u8bdd\u6301\u4e45\u5316\u6821\u9a8c\u5931\u8d25\uff1a%s", ", ".join(failures))
        raise RandomIPAuthError(f"{_SESSION_PERSIST_FAILED_DETAIL}:{','.join(failures)}")

def _set_session(new_session: RandomIPSession, *, verify_auth_persistence: bool = False) -> RandomIPSession:
    global _session
    with _session_lock:
        _ensure_loaded()
        previous_session = _session
        normalized_remaining, normalized_total, normalized_used = _normalize_quota_state(
            remaining_quota=new_session.remaining_quota,
            total_quota=new_session.total_quota,
            used_quota=new_session.used_quota,
        )
        candidate = replace(
            new_session,
            remaining_quota=normalized_remaining,
            total_quota=normalized_total,
            used_quota=normalized_used,
            quota_known=_normalize_quota_known(
                user_id=new_session.user_id,
                total_quota=normalized_total,
                used_quota=normalized_used,
                quota_known=new_session.quota_known,
            ),
        )
        _session = candidate
        try:
            _persist_session_locked()
            if verify_auth_persistence:
                _verify_persisted_session(candidate)
            return _session
        except Exception:
            _session = previous_session
            raise

def _read_session() -> RandomIPSession:
    _ensure_loaded()
    with _session_lock:
        return replace(_session)

def get_device_id() -> str:
    return _read_session().device_id

def clear_session(*, reason: str = "unspecified") -> None:
    global _session
    with _session_lock:
        _ensure_loaded()
        previous_session = _session
        _session = RandomIPSession(device_id=_session.device_id)
        settings = _get_settings()
        settings.remove(_settings_key("user_id"))
        settings.remove(_settings_key("remaining_quota"))
        settings.remove(_settings_key("total_quota"))
        settings.remove(_settings_key("used_quota"))
        settings.remove(_settings_key("quota_known"))
        settings.sync()
        _log_session_event(logging.WARNING, "\u672c\u5730\u4f1a\u8bdd\u5df2\u6e05\u7a7a", previous_session, reason=reason)

def has_authenticated_session() -> bool:
    session = _read_session()
    return _has_complete_session(session)

def get_session_snapshot() -> Dict[str, Any]:
    session = _read_session()
    quota = _build_quota_snapshot(session)
    return {
        "authenticated": _has_complete_session(session),
        "device_id": session.device_id,
        "user_id": int(session.user_id or 0),
        "remaining_quota": quota["remaining_quota"],
        "total_quota": quota["total_quota"],
        "used_quota": quota["used_quota"],
        "quota_known": bool(quota.get("quota_known")),
        "has_access_token": False,
        "has_refresh_token": False,
        "has_valid_user_id": _is_valid_user_id(session.user_id),
        "session_state": _session_state_name(session),
    }

def has_unknown_local_quota(snapshot: Optional[Dict[str, Any]] = None) -> bool:
    
    payload = snapshot if isinstance(snapshot, dict) else get_session_snapshot()
    if not bool(payload.get("authenticated")):
        return False
    if "quota_known" in payload:
        return not bool(payload.get("quota_known"))
    user_id = _to_non_negative_int(payload.get("user_id"), 0)
    total_quota = _to_non_negative_quota(payload.get("total_quota"), 0.0)
    used_quota = _to_non_negative_quota(payload.get("used_quota"), 0.0)
    return user_id > 0 and total_quota <= 0 and used_quota <= 0

def is_quota_exhausted(snapshot: Optional[Dict[str, Any]] = None) -> bool:
    payload = snapshot if isinstance(snapshot, dict) else get_session_snapshot()
    if not bool(payload.get("authenticated")):
        return False
    if "quota_known" in payload and not bool(payload.get("quota_known")):
        return False
    total_quota = _to_non_negative_quota(payload.get("total_quota"), 0.0)
    used_quota = _to_non_negative_quota(payload.get("used_quota"), 0.0)
    return total_quota > 0 and used_quota >= total_quota

def _require_authenticated_session() -> RandomIPSession:
    session = _read_session()
    if _has_complete_session(session):
        return session
    raise RandomIPAuthError("not_authenticated")

def load_session_for_startup() -> None:
    try:
        _ensure_loaded()
    except Exception as exc:
        log_suppressed_exception("auth.load_session_for_startup", exc, level=logging.WARNING)


from .auth_errors import format_random_ip_error  # noqa: E402
from .auth_operations import (  # noqa: E402
    activate_trial_async,
    claim_easter_egg_bonus_async,
    extract_proxy_async,
    get_fresh_quota_snapshot,
    get_quota_snapshot,
    redeem_card_async,
    sync_quota_snapshot_from_server_async,
)
