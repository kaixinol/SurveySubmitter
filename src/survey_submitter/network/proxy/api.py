from __future__ import annotations

import json
import logging
import re
import threading
from urllib.parse import parse_qsl, urlsplit
from typing import Any

import survey_submitter.network.http as http_client
from survey_submitter.core.task import ProxyLease
from survey_submitter.constants import (
    DEFAULT_HTTP_HEADERS,
    PROXY_MAX_PROXIES,
    PROXY_SOURCE_CUSTOM,
)
from survey_submitter.logging.log_utils import (
    log_popup_error,
)


from survey_submitter.network.proxy.source import (
    get_effective_proxy_api_url,
    get_proxy_occupy_minute,
    has_custom_proxy_api_override,
)


from survey_submitter.network.proxy.pool import (
    _build_proxy_lease,
    _mask_proxy_for_log,
)

_IP_PORT_RE = re.compile(
    r"(?:https?://)?"
    r"(?:([^\s:@/,]+):([^\s:@/,]+)@)?"
    r"((?:\d{1,3}\.){3}\d{1,3})"
    r":(\d{2,5})"
)

_FATAL_PATTERNS = [
    (r"白名单", "请先添加当前IP到代理商白名单"),
    (r"secret.*密匙错误", "API密钥错误，请检查配置"),
    (r"套餐余量不足", "套餐余量不足，请充值"),
    (r"套餐已过期", "套餐已过期，请续费"),
    (r"套餐被禁用", "套餐已被禁用，请联系代理商"),
    (r"身份未认证", "请先完成实名认证"),
    (r"用户被禁用", "账号已被禁用，请联系代理商"),
]


class ProxyApiFatalError(RuntimeError):
    pass


def _normalize_expected_proxy_count(expected_count: Any) -> int:
    try:
        parsed = int(expected_count)
    except (ValueError, TypeError):
        parsed = 1
    return max(1, min(PROXY_MAX_PROXIES, parsed))


def _extract_proxy_from_string(s: str) -> str | None:
    if not isinstance(s, str):
        return None
    m = _IP_PORT_RE.search(s.strip())
    if not m:
        return None
    user, pwd, ip, port = m.group(1), m.group(2), m.group(3), m.group(4)
    return f"{user}:{pwd}@{ip}:{port}" if user and pwd else f"{ip}:{port}"


def _extract_proxy_from_dict(obj: dict) -> str | None:
    if not isinstance(obj, dict):
        return None
    ip = str(obj.get("ip") or obj.get("IP") or obj.get("host") or "").strip()
    port = str(obj.get("port") or obj.get("Port") or obj.get("PORT") or "").strip()
    if ip and port:
        username = str(obj.get("account") or obj.get("username") or obj.get("user") or "").strip()
        password = str(obj.get("password") or obj.get("pwd") or obj.get("pass") or "").strip()
        return f"{username}:{password}@{ip}:{port}" if username and password else f"{ip}:{port}"
    for v in obj.values():
        if isinstance(v, str):
            proxy = _extract_proxy_from_string(v)
            if proxy:
                return proxy
    return None


def _recursive_find_proxies(data: Any, results: list[str], depth: int = 0) -> None:
    if depth > 10:
        return
    if isinstance(data, dict):
        proxy = _extract_proxy_from_dict(data)
        if proxy:
            results.append(proxy)
            return
        for value in data.values():
            _recursive_find_proxies(value, results, depth + 1)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                proxy = _extract_proxy_from_string(item)
                if proxy:
                    results.append(proxy)
            else:
                _recursive_find_proxies(item, results, depth + 1)
    elif isinstance(data, str):
        proxy = _extract_proxy_from_string(data)
        if proxy:
            results.append(proxy)


def _parse_proxy_payload(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON解析失败: {e}")
    candidates: list[str] = []
    _recursive_find_proxies(data, candidates)
    if not candidates:
        raise ValueError("返回数据中无有效代理地址")
    seen: set[str] = set()
    unique: list[str] = []
    for addr in candidates:
        if addr not in seen:
            seen.add(addr)
            unique.append(addr)
            logging.info(f"获取到代理: {_mask_proxy_for_log(addr)}")
    return unique


def _extract_custom_api_error(data: Any) -> str | None:

    if not isinstance(data, dict):
        return None
    code = data.get("code")
    if code == 0:
        return None
    message = str(data.get("message") or "").strip()
    if not message:
        return None
    for pattern, user_msg in _FATAL_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return user_msg
    return None


def _proxy_api_candidates(proxy_url: str | None) -> list[str]:
    url = proxy_url or get_effective_proxy_api_url()
    if not url:
        raise RuntimeError("自定义代理API地址不能为空，请先在设置中填写API地址")
    return [url]


def _warn_custom_api_returned_large_batch(returned_count: int, requested_count: int) -> None:
    requested = max(1, int(requested_count or 1))
    returned = max(0, int(returned_count or 0))
    if returned <= int(requested * 1.2):
        return
    logging.warning(
        "自定义代理API返回 %s 个有效代理，当前运行本轮请求 %s 个，将缓存到代理池并按任务并发逐个使用。可能会引发代理池余额浪费",
        returned,
        requested,
    )


def _extract_minute_from_url(url: str) -> int | None:

    try:
        split = urlsplit(url)
        for key, value in parse_qsl(split.query):
            if key.lower() == "minute":
                return int(value)
    except (ValueError, TypeError):
        pass
    return None


def _check_minute_conflict(url: str) -> str | None:

    minute = _extract_minute_from_url(url)
    if minute is None:
        return None
    occupy_minute = get_proxy_occupy_minute()
    if minute < occupy_minute:
        return f"代理时长 ({minute}分钟) 小于当前建议值 ({occupy_minute}分钟，已含安全缓冲)，可能导致作答过程中代理失效"
    return None


def test_custom_proxy_api(url: str) -> tuple[bool, str, list[str]]:
    if not url or not url.strip():
        return False, "API地址不能为空", []
    url = url.strip()
    if not (url.lower().startswith("http://") or url.lower().startswith("https://")):
        return False, "API地址必须以 http:// 或 https:// 开头", []
    try:
        resp = http_client.get(url, timeout=10, headers=DEFAULT_HTTP_HEADERS)
        resp.raise_for_status()
    except http_client.Timeout:
        return False, "请求超时，请检查网络或API地址", []
    except http_client.ConnectionError:
        return False, "连接失败，请检查API地址是否正确", []
    except http_client.HTTPError as e:
        return False, f"HTTP错误: {e.response.status_code}", []
    except OSError as e:
        return False, f"请求失败: {e}", []

    try:
        data = json.loads(resp.text)
        error = _extract_custom_api_error(data)
        if error:
            return False, error, []
    except (ValueError, KeyError):
        pass

    try:
        proxies = _parse_proxy_payload(resp.text)
        if not proxies:
            return False, "未能从返回数据中解析出代理地址", []
        warning = _check_minute_conflict(url)
        return True, warning or "", proxies
    except ValueError as e:
        return False, str(e), []
    except (KeyError, TypeError) as e:
        return False, f"解析失败: {e}", []


async def fetch_proxy_batch_async(
    expected_count: int = 1,
    *,
    proxy_url: str | None = None,
    stop_signal: threading.Event | None = None,
) -> list[ProxyLease]:
    expected_count = _normalize_expected_proxy_count(expected_count)

    if not has_custom_proxy_api_override():
        raise RuntimeError("自定义代理API地址未配置，请在设置中填写API地址")
    url = proxy_url or get_effective_proxy_api_url()
    if not url:
        raise RuntimeError("自定义代理API地址未配置，请在设置中填写API地址")
    logging.info(f"使用自定义代理API: {url}")

    candidates: list[str] = []
    errors: list[str] = []
    for candidate_url in _proxy_api_candidates(url):
        try:
            resp = await http_client.aget(
                candidate_url,
                timeout=10,
                headers=DEFAULT_HTTP_HEADERS,
            )
            resp.raise_for_status()

            try:
                payload = json.loads(resp.text)
                error = _extract_custom_api_error(payload)
                if error:
                    log_popup_error("代理API错误", error)
                    if stop_signal and not stop_signal.is_set():
                        stop_signal.set()
                    raise ProxyApiFatalError(error)
            except (json.JSONDecodeError, ProxyApiFatalError):
                raise
            except (ValueError, KeyError, TypeError):
                pass

            parsed = _parse_proxy_payload(resp.text)
            candidates.extend(parsed)
            if candidates:
                break
        except ProxyApiFatalError:
            raise
        except (http_client.HTTPError, OSError) as exc:
            errors.append(str(exc))
            continue
    if not candidates:
        raise RuntimeError(f"获取随机IP失败: {'; '.join(errors) if errors else '无可用接口'}")
    seen: set[str] = set()
    normalized: list[ProxyLease] = []
    for item in candidates:
        lease = _build_proxy_lease(item, source=PROXY_SOURCE_CUSTOM)
        if lease is None:
            continue
        addr = lease.address
        if not addr or addr in seen:
            continue
        seen.add(addr)
        normalized.append(lease)
        if len(normalized) >= PROXY_MAX_PROXIES:
            break
    if not normalized:
        raise RuntimeError("随机IP接口返回为空")
    _warn_custom_api_returned_large_batch(len(normalized), expected_count)
    return normalized


__all__ = [
    "ProxyApiFatalError",
    "fetch_proxy_batch_async",
    "test_custom_proxy_api",
]
