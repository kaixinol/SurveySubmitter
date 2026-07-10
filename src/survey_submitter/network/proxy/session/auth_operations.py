from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from survey_submitter.constants import (
    AUTH_BONUS_CLAIM_ENDPOINT,
    AUTH_TRIAL_ENDPOINT,
    CARD_REDEEM_ENDPOINT,
    IP_EXTRACT_ENDPOINT,
)

from .client import (
    _apost_json,
    _extract_error_payload,
    _log_extract_proxy_issue,
    _parse_batch_extract_payload,
    _parse_single_extract_payload,
)
from .models import RandomIPAuthError, RandomIPSession
from .normalize import (
    _build_quota_snapshot,
    _is_valid_user_id,
    _normalize_quota_known,
    _normalize_quota_state,
    _require_valid_user_id,
    _resolve_quota_from_payload,
    _session_log_fields,
    _to_non_negative_int,
    _to_non_negative_quota,
)
from .auth import (
    _endpoint_name,
    _extract_request_timeout_seconds,
    _log_session_event,
    _read_session,
    _require_authenticated_session,
    _set_session,
)


async def activate_trial_async() -> RandomIPSession:
    logging.info("\u968f\u673aIP\u8bd5\u7528\u9886\u53d6\u5f00\u59cb\uff1aendpoint=%s", _endpoint_name(AUTH_TRIAL_ENDPOINT))
    response = await _apost_json(AUTH_TRIAL_ENDPOINT, json_body={})
    if int(getattr(response, "status_code", 0) or 0) != 200:
        error = _extract_error_payload(response)
        logging.warning(
            "\u968f\u673aIP\u8bd5\u7528\u9886\u53d6\u5931\u8d25\uff1aendpoint=%s status=%s detail=%s",
            _endpoint_name(AUTH_TRIAL_ENDPOINT),
            int(getattr(response, "status_code", 0) or 0),
            error.detail,
        )
        raise error
    session = await _parse_session_response(response)
    try:
        persisted = _set_session(session, verify_auth_persistence=True)
        _log_session_event(logging.INFO, "\u8bd5\u7528\u9886\u53d6\u6210\u529f\u5e76\u5df2\u4fdd\u5b58", persisted)
        return persisted
    except RandomIPAuthError as exc:
        from .auth import _SESSION_PERSIST_FAILED_DETAIL, clear_session
        if exc.detail.startswith(_SESSION_PERSIST_FAILED_DETAIL):
            clear_session(reason="trial_persist_failed")
        logging.warning("\u968f\u673aIP\u8bd5\u7528\u9886\u53d6\u540e\u4fdd\u5b58\u5931\u8d25\uff1adetail=%s", exc.detail)
        raise


async def extract_proxy_async(
    *,
    minute: int,
    pool: str,
    area: Optional[str],
    num: int = 1,
    upstream: str = "default",
) -> Dict[str, Any]:
    session = _require_authenticated_session()
    body: Dict[str, Any] = {
        "user_id": int(session.user_id),
        "minute": int(minute),
        "pool": str(pool or "").strip(),
    }
    upstream_value = str(upstream or "").strip().lower()
    if upstream_value:
        body["upstream"] = upstream_value
    request_num = max(1, int(num or 1))
    if request_num > 1:
        body["num"] = request_num
    area_code = str(area or "").strip()
    if area_code:
        body["area"] = area_code

    try:
        response = await _apost_json(
            IP_EXTRACT_ENDPOINT,
            json_body=body,
            timeout=_extract_request_timeout_seconds(request_num),
        )
    except RandomIPAuthError as exc:
        _log_extract_proxy_issue("\u968f\u673aIP\u63d0\u53d6\u8bf7\u6c42\u5f02\u5e38", request_body=body, attempt=1, error=exc)
        raise
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code == 200:
        try:
            data = response.json()
        except Exception as exc:
            _log_extract_proxy_issue("\u968f\u673aIP\u63d0\u53d6\u54cd\u5e94\u89e3\u6790\u5931\u8d25", request_body=body, attempt=1, response=response, error=exc)
            raise RandomIPAuthError(f"invalid_response:{exc}") from exc
        if not isinstance(data, dict):
            _log_extract_proxy_issue("\u968f\u673aIP\u63d0\u53d6\u54cd\u5e94\u7ed3\u6784\u5f02\u5e38", request_body=body, attempt=1, response=response)
            raise RandomIPAuthError("invalid_response")
        if request_num > 1 and isinstance(data.get("items"), list):
            return _parse_batch_extract_payload(
                data,
                request_body=body,
                attempt=1,
                response=response,
            )
        return _parse_single_extract_payload(
            data,
            request_body=body,
            attempt=1,
            response=response,
        )
    error = _extract_error_payload(response)
    _log_extract_proxy_issue("\u968f\u673aIP\u63d0\u53d6\u5931\u8d25", request_body=body, attempt=1, response=response, error=error)
    raise error


def get_quota_snapshot() -> Dict[str, Any]:
    return _build_quota_snapshot(_read_session())


def get_fresh_quota_snapshot() -> Dict[str, Any]:
    return _build_quota_snapshot(_require_authenticated_session())


async def sync_quota_snapshot_from_server_async(*, emit_logs: bool = True) -> Dict[str, Any]:
    session = _require_authenticated_session()
    if emit_logs:
        logging.info(
            "\u968f\u673aIP\u989d\u5ea6\u670d\u52a1\u7aef\u540c\u6b65\u5f00\u59cb\uff1aendpoint=%s user_id=%s",
            _endpoint_name(AUTH_TRIAL_ENDPOINT),
            int(session.user_id or 0),
        )
    response = await _apost_json(AUTH_TRIAL_ENDPOINT, json_body={})
    if int(getattr(response, "status_code", 0) or 0) != 200:
        error = _extract_error_payload(response)
        if emit_logs:
            logging.warning(
                "\u968f\u673aIP\u989d\u5ea6\u670d\u52a1\u7aef\u540c\u6b65\u5931\u8d25\uff1aendpoint=%s status=%s detail=%s",
                _endpoint_name(AUTH_TRIAL_ENDPOINT),
                int(getattr(response, "status_code", 0) or 0),
                error.detail,
            )
        raise error
    refreshed = await _parse_session_response(response, fallback_session=session)
    persisted = _set_session(refreshed, verify_auth_persistence=True)
    if emit_logs:
        _log_session_event(logging.INFO, "\u968f\u673aIP\u989d\u5ea6\u5df2\u4e0e\u670d\u52a1\u7aef\u540c\u6b65", persisted)
    return _build_quota_snapshot(persisted)


def _apply_quota_payload(data: Dict[str, Any], *, log_context: str = "\u968f\u673aIP\u989d\u5ea6\u54cd\u5e94") -> RandomIPSession:
    session = _read_session()
    normalized_remaining, normalized_total, normalized_used, quota_known = _resolve_quota_from_payload(
        data,
        fallback_session=session,
        log_context=log_context,
    )
    updated = replace(
        session,
        remaining_quota=normalized_remaining,
        total_quota=normalized_total,
        used_quota=normalized_used,
        quota_known=quota_known,
    )
    return _set_session(updated)


async def claim_easter_egg_bonus_async() -> Dict[str, Any]:
    session = _require_authenticated_session()
    response = await _apost_json(
        AUTH_BONUS_CLAIM_ENDPOINT,
        json_body={
            "user_id": int(session.user_id),
            "bonus_code": "fuck-you-hacker",
        },
    )
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise _extract_error_payload(response)
    try:
        data = response.json()
    except Exception as exc:
        raise RandomIPAuthError(f"invalid_response:{exc}") from exc
    if not isinstance(data, dict):
        raise RandomIPAuthError("invalid_response")

    session = _apply_quota_payload(data, log_context="\u968f\u673aIP\u5f69\u86cb\u989d\u5ea6\u54cd\u5e94")
    claimed = bool(data.get("claimed", False))
    bonus_quota = _to_non_negative_quota(data.get("bonus_quota"), 0.0)
    detail = str(data.get("detail") or "").strip()
    return {
        "claimed": claimed,
        "bonus_quota": bonus_quota,
        "detail": detail,
        "used_quota": session.used_quota,
        "remaining_quota": session.remaining_quota,
        "total_quota": session.total_quota,
    }


async def redeem_card_async(card_code: str) -> Dict[str, Any]:
    session = _require_authenticated_session()
    normalized_card_code = str(card_code or "").strip()
    response = await _apost_json(
        CARD_REDEEM_ENDPOINT,
        json_body={
            "user_id": int(session.user_id),
            "card_code": normalized_card_code,
        },
    )
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise _extract_error_payload(response)
    try:
        data = response.json()
    except Exception as exc:
        raise RandomIPAuthError(f"invalid_response:{exc}") from exc
    if not isinstance(data, dict):
        raise RandomIPAuthError("invalid_response")

    updated_session = _apply_quota_payload(data, log_context="\u968f\u673aIP\u989d\u5ea6\u5361\u5bc6\u5151\u6362\u54cd\u5e94")
    redeemed = bool(data.get("redeemed"))
    card_quota = _to_non_negative_quota(data.get("card_quota"), 0.0)
    detail = str(data.get("detail") or "").strip()
    return {
        "redeemed": redeemed,
        "card_quota": card_quota,
        "detail": detail,
        "used_quota": updated_session.used_quota,
        "remaining_quota": updated_session.remaining_quota,
        "total_quota": updated_session.total_quota,
    }


def _parse_session_payload(
    data: Dict[str, Any],
    *,
    device_id: str,
    fallback_session: Optional[RandomIPSession] = None,
) -> RandomIPSession:
    fallback = fallback_session or RandomIPSession(device_id=device_id)
    if "user_id" not in data:
        logging.warning("\u968f\u673aIP\u4f1a\u8bdd\u54cd\u5e94\u7f3a\u5c11 user_id\uff1akeys=%s", ",".join(sorted(str(k) for k in data.keys())))
        raise RandomIPAuthError("invalid_response:user_id_missing")
    raw_user_id = data.get("user_id")
    if not _is_valid_user_id(raw_user_id):
        logging.warning(
            "\u968f\u673aIP\u4f1a\u8bdd\u54cd\u5e94\u4e2d\u7684 user_id \u65e0\u6548\uff1avalue=%r type=%s keys=%s",
            raw_user_id,
            type(raw_user_id).__name__,
            ",".join(sorted(str(k) for k in data.keys())),
        )
        raise RandomIPAuthError("invalid_response:user_id_invalid")
    normalized_remaining, normalized_total, normalized_used, quota_known = _resolve_quota_from_payload(
        data,
        fallback_session=fallback,
        log_context="\u968f\u673aIP\u4f1a\u8bdd\u54cd\u5e94",
    )
    session = RandomIPSession(
        device_id=device_id,
        user_id=_require_valid_user_id(raw_user_id),
        remaining_quota=normalized_remaining,
        total_quota=normalized_total,
        used_quota=normalized_used,
        quota_known=quota_known,
    )
    return session


async def _parse_session_response(response: Any, *, fallback_session: Optional[RandomIPSession] = None) -> RandomIPSession:
    try:
        data = response.json()
    except Exception as exc:
        raise RandomIPAuthError(f"invalid_response:{exc}") from exc
    if not isinstance(data, dict):
        raise RandomIPAuthError("invalid_response")
    from .auth import get_device_id
    device_id = fallback_session.device_id if fallback_session is not None else get_device_id()
    return _parse_session_payload(data, device_id=device_id, fallback_session=fallback_session)
