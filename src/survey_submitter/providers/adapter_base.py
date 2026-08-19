from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from survey_submitter.core.task import ExecutionConfig, ExecutionState
from survey_submitter.providers.contracts import SurveyDefinition

ParseSurveyHook = Callable[[str], Awaitable[SurveyDefinition]]
FillSurveyHttpHook = Callable[..., Awaitable[bool]]


async def _return_false(*_args: Any, **_kwargs: Any) -> bool:
    return False


@dataclass(frozen=True)
class ProviderAdapterHooks:
    parse_survey: ParseSurveyHook
    fill_survey_http: FillSurveyHttpHook = _return_false


class CallableProviderAdapter:
    def __init__(self, provider: str, hooks: ProviderAdapterHooks) -> None:
        self.provider = provider or ""
        self._hooks = hooks

    async def parse_survey_async(self, url: str) -> SurveyDefinition:
        return await self._hooks.parse_survey(url)

    async def fill_survey_http_async(
        self,
        config: ExecutionConfig,
        state: ExecutionState,
        *,
        stop_signal: Any = None,
        thread_name: str = "",
        proxy_address: str | None = None,
        user_agent: str | None = None,
        user_agent_profile: Any = None,
        submit_proxy_lease_factory: Any = None,
    ) -> bool:
        return bool(
            await self._hooks.fill_survey_http(
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


__all__ = [
    "CallableProviderAdapter",
    "ProviderAdapterHooks",
]
