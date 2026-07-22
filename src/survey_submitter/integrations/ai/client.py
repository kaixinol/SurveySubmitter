from __future__ import annotations

from loguru import logger

from survey_submitter.core.task import ExecutionState
from survey_submitter.integrations.ai.protocols import (
    RESPONSES_SUFFIX,
    acall_chat_completions,
    acall_responses,
    is_endpoint_mismatch_error,
    normalize_endpoint_url,
    resolve_custom_endpoint,
)
from survey_submitter.integrations.ai.settings import (
    CUSTOM_API_PROTOCOLS,
    DEFAULT_SYSTEM_PROMPT,
    _normalize_custom_api_protocol,
    get_ai_readiness_error,
    get_ai_settings,
    get_default_system_prompt,
    save_ai_settings,
)

__all__ = [
    "CUSTOM_API_PROTOCOLS",
    "DEFAULT_SYSTEM_PROMPT",
    "agenerate_answer",
    "get_ai_readiness_error",
    "get_ai_settings",
    "get_default_system_prompt",
    "save_ai_settings",
    "atest_connection",
]


async def agenerate_answer(
    question_title: str,
    *,
    question_type: str = "fill_blank",
    blank_count: int | None = None,
    state: ExecutionState | None = None,
) -> str | list[str]:

    config = get_ai_settings()
    readiness_error = get_ai_readiness_error(config)
    if readiness_error:
        raise RuntimeError(f"AI 配置不完整：{readiness_error}")

    api_key = str(config["api_key"] or "")
    base_url = str(config["base_url"] or "")
    model = str(config["model"] or "")
    system_prompt = str(config["system_prompt"] or "").strip() or get_default_system_prompt()

    api_protocol = _normalize_custom_api_protocol(config["api_protocol"])
    resolved_protocol, request_url, has_explicit_endpoint = resolve_custom_endpoint(
        base_url, api_protocol
    )

    if resolved_protocol == "responses":
        return await acall_responses(request_url, api_key, model, question_title, system_prompt)
    try:
        return await acall_chat_completions(
            request_url, api_key, model, question_title, system_prompt
        )
    except Exception as exc:
        if has_explicit_endpoint or api_protocol != "auto" or not is_endpoint_mismatch_error(exc):
            raise
        fallback_url = f"{normalize_endpoint_url(base_url)}{RESPONSES_SUFFIX}"
        return await acall_responses(
            fallback_url, api_key, model, question_title, system_prompt
        )


async def atest_connection() -> str:

    try:
        logger.info("AI 连接测试开始")
        result = await agenerate_answer(
            "这是一个测试问题，请回复'连接成功'",
            question_type="fill_blank",
            blank_count=1,
        )
        if isinstance(result, list):
            preview = " | ".join(result[:3])
        else:
            preview = str(result)
        logger.info(f"AI 连接测试成功 | preview={preview[:80]}")
        return f"连接成功！AI 回复: {preview[:50]}..."
    except Exception as exc:
        logger.error(f"AI 连接测试失败: {exc}")
        return f"连接失败: {exc}"
