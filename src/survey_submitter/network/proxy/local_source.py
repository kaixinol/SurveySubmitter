from __future__ import annotations

import os
from urllib.parse import urlsplit

from loguru import logger

import survey_submitter.network.http as http_client
from survey_submitter.constants import PROXY_SOURCE_FETCH_TIMEOUT
from survey_submitter.network.proxy.pool import normalize_proxy_address


def _is_url_source(value: str) -> bool:
    lowered = value.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return False
    try:
        parsed = urlsplit(lowered)
    except ValueError:
        return False
    return bool(parsed.scheme and parsed.hostname)


def _is_file_source(value: str) -> bool:
    try:
        return os.path.isfile(value)
    except (ValueError, OSError):
        return False


def _read_source_text(source: str) -> str:
    if _is_url_source(source):
        try:
            response = http_client.get(source, timeout=PROXY_SOURCE_FETCH_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"读取代理列表链接失败：{source}（{exc}）") from exc
        if response.status_code >= 400:
            raise RuntimeError(
                f"读取代理列表链接返回状态码 {response.status_code}：{source}"
            )
        return response.text or ""
    if _is_file_source(source):
        try:
            with open(source, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError as exc:
            raise RuntimeError(f"读取代理列表文件失败：{source}（{exc}）") from exc
    # 字面代理地址（如 "1.2.3.4:8080"），原样作为单条返回
    return source


def _tokenize_proxy_text(text: str) -> list[str]:
    addresses: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line
        if not line or line.startswith("#"):
            continue
        for token in line.replace(",", " ").split():
            token = token
            if token:
                addresses.append(token)
    return addresses


def resolve_local_proxy_addresses(raw: str | list[str]) -> list[str]:
    """展开 ``source=local`` 的 ``ip_list`` 配置。

    支持：字面代理地址、本地文件路径、http(s) 链接（单 str 或 list[str]）。
    文件/链接内容按行解析（每行一个代理，忽略空行与 ``#`` 注释），多源合并后去重。
    """
    if isinstance(raw, str):
        sources: list[str] = [raw] if raw else []
    elif isinstance(raw, (list, tuple)):
        sources = [str(item) for item in raw if item is not None]
        sources = [item for item in sources if item]
    else:
        sources = [str(raw)] if str(raw) else []

    seen: set[str] = set()
    resolved: list[str] = []
    for source in sources:
        try:
            body = _read_source_text(source)
        except RuntimeError as exc:
            logger.warning(str(exc))
            raise
        for token in _tokenize_proxy_text(body):
            normalized = normalize_proxy_address(token)
            if not normalized:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            resolved.append(normalized)
    return resolved


__all__ = ["resolve_local_proxy_addresses"]
