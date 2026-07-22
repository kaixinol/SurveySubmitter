from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from openai import APIError, APIStatusError, AsyncOpenAI

_CHAT_COMPLETIONS_SUFFIX = "/chat/completions"
RESPONSES_SUFFIX = "/responses"
_LEGACY_COMPLETIONS_SUFFIX = "/completions"
_AI_REQUEST_TIMEOUT_SECONDS = 30
_AI_MAX_RETRY_ATTEMPTS = 2

__all__ = [
    "_AI_REQUEST_TIMEOUT_SECONDS",
    "_CHAT_COMPLETIONS_SUFFIX",
    "RESPONSES_SUFFIX",
    "extract_chat_completion_text",
    "extract_responses_text",
    "is_endpoint_mismatch_error",
    "normalize_endpoint_url",
    "resolve_custom_endpoint",
    "acall_chat_completions",
    "acall_responses",
]


def normalize_endpoint_url(raw_url: str) -> str:
    return str(raw_url or "").strip().rstrip("/")


def _path_endswith(path: str, suffix: str) -> bool:
    normalized_path = (path or "").rstrip("/").lower()
    return normalized_path.endswith(suffix)


def _replace_path_suffix(url_parts, suffix: str) -> str:
    normalized_path = (url_parts.path or "").rstrip("/")
    return urlunsplit(
        (
            url_parts.scheme,
            url_parts.netloc,
            normalized_path + suffix,
            url_parts.query,
            url_parts.fragment,
        )
    )


def _strip_endpoint_suffix(url: str) -> str:
    parts = urlsplit(url)
    path = (parts.path or "").rstrip("/")
    for suffix in (_CHAT_COMPLETIONS_SUFFIX, RESPONSES_SUFFIX):
        if _path_endswith(path, suffix):
            path = path[: -len(suffix)]
            return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
    return url


def resolve_custom_endpoint(base_url: str, api_protocol: str) -> tuple[str, str, bool]:
    normalized_base_url = normalize_endpoint_url(base_url)
    if not normalized_base_url:
        raise RuntimeError("自定义模式需要配置 Base URL")

    parts = urlsplit(normalized_base_url)
    path = parts.path or ""

    if _path_endswith(path, _CHAT_COMPLETIONS_SUFFIX):
        return "chat_completions", normalized_base_url, True
    if _path_endswith(path, RESPONSES_SUFFIX):
        return "responses", normalized_base_url, True
    if _path_endswith(path, _LEGACY_COMPLETIONS_SUFFIX):
        raise RuntimeError("暂不支持旧版 /completions 协议，请改用 /chat/completions 或 /responses")

    if str(api_protocol or "auto").strip().lower() == "responses":
        return "responses", _replace_path_suffix(parts, RESPONSES_SUFFIX), False
    return "chat_completions", _replace_path_suffix(parts, _CHAT_COMPLETIONS_SUFFIX), False


def is_endpoint_mismatch_error(exc: Exception) -> bool:
    if isinstance(exc, APIStatusError):
        if exc.status_code in {404, 405, 410}:
            return True
    message = str(exc or "").lower()
    mismatch_markers = (
        "404",
        "405",
        "410",
        "not found",
        "no route",
        "no handler",
        "unsupported path",
        "invalid url",
        "method not allowed",
    )
    return any(marker in message for marker in mismatch_markers)


def _extract_text_parts(content: Any) -> Iterable[str]:
    if isinstance(content, str):
        text = content.strip()
        if text:
            yield text
        return

    if not isinstance(content, list):
        return

    for item in content:
        if isinstance(item, str):
            text = item.strip()
            if text:
                yield text
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        text = str(item.get("text") or item.get("content") or "").strip()
        if item_type in {"text", "output_text", "input_text"} and text:
            yield text


def extract_chat_completion_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("API 返回中缺少 choices")

    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content")
    parts = list(_extract_text_parts(content))
    if parts:
        return "\n".join(parts).strip()
    raise RuntimeError("API 返回内容为空")


def extract_responses_text(data: dict[str, Any]) -> str:
    top_level_text = str(data.get("output_text") or "").strip()
    if top_level_text:
        return top_level_text

    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            parts = list(_extract_text_parts(item.get("content")))
            if parts:
                return "\n".join(parts).strip()

    raise RuntimeError("Responses API 返回内容为空")


async def acall_chat_completions(
    url: str,
    api_key: str,
    model: str,
    question: str,
    system_prompt: str,
    timeout: int = _AI_REQUEST_TIMEOUT_SECONDS,
) -> str:
    base_url = _strip_endpoint_suffix(url)
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=float(timeout),
        max_retries=_AI_MAX_RETRY_ATTEMPTS,
    )
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请简短回答这个问卷问题：{question}"},
            ],
            max_tokens=200,
            temperature=0.7,
        )
        content = response.choices[0].message.content
        if content:
            return content.strip()
        raise RuntimeError("API 返回内容为空")
    except APIError as exc:
        raise RuntimeError(f"API 调用失败: {exc}") from exc


async def acall_responses(
    url: str,
    api_key: str,
    model: str,
    question: str,
    system_prompt: str,
    timeout: int = _AI_REQUEST_TIMEOUT_SECONDS,
) -> str:
    base_url = _strip_endpoint_suffix(url)
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=float(timeout),
        max_retries=_AI_MAX_RETRY_ATTEMPTS,
    )
    try:
        response = await client.responses.create(
            model=model,
            instructions=system_prompt,
            input=f"请简短回答这个问卷问题：{question}",
            max_output_tokens=200,
            temperature=0.7,
        )
        text = str(response.output_text or "").strip()
        if text:
            return text
        raise RuntimeError("Responses API 返回内容为空")
    except APIError as exc:
        raise RuntimeError(f"API 调用失败: {exc}") from exc
