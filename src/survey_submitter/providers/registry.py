from __future__ import annotations

from typing import Any

from survey_submitter.core.engine.provider_common import provider_run_context
from survey_submitter.providers.adapter_base import CallableProviderAdapter, ProviderAdapterHooks
from survey_submitter.providers.common import (
    SURVEY_PROVIDER_WJX,
    detect_survey_provider,
    normalize_survey_parse_url,
    normalize_survey_provider,
)
from survey_submitter.providers.contracts import SurveyDefinition
from survey_submitter.providers.hooks import (
    HookTarget,
    build_fill_http_hook,
    build_parse_hook,
)


def _resolve_provider(*, provider: str | None = None, ctx: Any = None) -> str:
    if provider is not None:
        return normalize_survey_provider(provider, default=SURVEY_PROVIDER_WJX)
    if ctx is not None:
        return normalize_survey_provider(
            getattr(getattr(ctx, "config", ctx), "survey_provider", None),
            default=SURVEY_PROVIDER_WJX,
        )
    return SURVEY_PROVIDER_WJX


_WJX_PARSE: HookTarget = ("survey_submitter.providers.wjx.parser", "parse_wjx_survey")

_WJX_FILL_HTTP: HookTarget = ("survey_submitter.providers.wjx.http_runtime", "brush_wjx_http")


_PROVIDER_REGISTRY = {
    SURVEY_PROVIDER_WJX: CallableProviderAdapter(
        SURVEY_PROVIDER_WJX,
        ProviderAdapterHooks(
            parse_survey=build_parse_hook(SURVEY_PROVIDER_WJX, _WJX_PARSE),
            fill_survey_http=build_fill_http_hook(_WJX_FILL_HTTP),
        ),
    ),
}


def _get_provider_adapter(*, provider: str | None = None, ctx: Any = None, url: str | None = None):
    resolved = _resolve_provider(provider=provider, ctx=ctx)
    if url:
        resolved = detect_survey_provider(url)
    adapter = _PROVIDER_REGISTRY.get(resolved)
    if adapter is None:
        raise RuntimeError(f"不支持的问卷 provider: {resolved}")
    return adapter


async def parse_survey(url: str) -> SurveyDefinition:

    normalized_url = normalize_survey_parse_url(url)
    return await _get_provider_adapter(url=normalized_url).parse_survey_async(normalized_url)


async def fill_survey_http(
    config: Any,
    state: Any,
    *,
    stop_signal: Any = None,
    thread_name: str = "",
    provider: str | None = None,
    proxy_address: str | None = None,
    user_agent: str | None = None,
    user_agent_profile: Any = None,
    submit_proxy_lease_factory: Any = None,
) -> bool:
    adapter = _get_provider_adapter(provider=provider, ctx=state)
    try:
        state.update_thread_status(thread_name, "构造答案", running=True)
    except Exception:
        pass
    with provider_run_context(
        config,
        state=state,
        thread_name=thread_name,
    ):
        return bool(
            await adapter.fill_survey_http_async(
                config,
                state,
                stop_signal=stop_signal,
                thread_name=thread_name,
                proxy_address=proxy_address,
                user_agent=user_agent,
                user_agent_profile=user_agent_profile,
                submit_proxy_lease_factory=submit_proxy_lease_factory,
            )
        )


async def is_completion_page(driver: Any, provider: str | None = None) -> bool:

    return bool(await _get_provider_adapter(provider=provider).is_completion_page_async(driver))


__all__ = [
    "SURVEY_PROVIDER_WJX",
    "SurveyDefinition",
    "detect_survey_provider",
    "parse_survey",
    "fill_survey_http",
    "is_completion_page",
]
