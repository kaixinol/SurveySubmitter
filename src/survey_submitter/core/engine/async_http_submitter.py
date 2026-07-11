from __future__ import annotations

from survey_submitter.core.config.codec import UserAgentProfile
from survey_submitter.core.engine.stop_signal import StopSignalLike
from survey_submitter.core.task import ExecutionConfig, ExecutionState
from survey_submitter.providers.common import (
    SURVEY_PROVIDER_WJX,
    normalize_survey_provider,
)
from survey_submitter.providers.http_logic import get_http_logic_fallback_reason
from survey_submitter.providers.registry import fill_survey_http

HTTP_RUNTIME_PROVIDERS = {
    SURVEY_PROVIDER_WJX,
}


class AsyncHttpSubmitter:
    

    def __init__(self, *, config: ExecutionConfig, state: ExecutionState, slot_label: str) -> None:
        self.config = config
        self.state = state
        self.slot_label = slot_label

    def uses_http_runtime(self) -> bool:
        provider = normalize_survey_provider(self.config.survey_provider)
        if not bool(str(self.config.url or "").strip()) or provider not in HTTP_RUNTIME_PROVIDERS:
            return False
        questions = list((self.config.questions_metadata or {}).values())
        return not bool(get_http_logic_fallback_reason(questions))

    def resolve_block_reason(self) -> str:
        provider = normalize_survey_provider(self.config.survey_provider)
        if provider not in HTTP_RUNTIME_PROVIDERS:
            return ""
        url = str(self.config.url or "").strip()
        if not url:
            return "问卷链接为空，无法进入纯 HTTP 提交"
        questions = list((self.config.questions_metadata or {}).values())
        reason = str(get_http_logic_fallback_reason(questions) or "").strip()
        if reason:
            return reason
        return ""

    async def submit(
        self,
        *,
        stop_signal: StopSignalLike,
        proxy_address: str | None,
        user_agent: str | None,
        user_agent_profile: UserAgentProfile | None = None,
        submit_proxy_lease_factory: object | None = None,
    ) -> bool:
        return bool(
            await fill_survey_http(
                self.config,
                self.state,
                stop_signal=stop_signal,
                thread_name=self.slot_label,
                provider=self.config.survey_provider,
                proxy_address=proxy_address,
                user_agent=user_agent,
                user_agent_profile=user_agent_profile,
                submit_proxy_lease_factory=submit_proxy_lease_factory,
            )
        )


__all__ = ["AsyncHttpSubmitter", "HTTP_RUNTIME_PROVIDERS"]
