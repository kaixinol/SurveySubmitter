from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from survey_submitter.core.ai.runtime import AIRuntimeError
from survey_submitter.core.engine.failure_reason import FailureReason
from survey_submitter.network.session_policy import (
    _discard_unresponsive_proxy,
    _mark_proxy_temporarily_bad,
    _record_bad_proxy_and_maybe_pause,
)
from survey_submitter.providers.errors import (
    SubmissionVerificationRequiredError,
    SurveyProviderUnavailableAtRuntimeError,
)
from survey_submitter.core.engine.runtime_error_handlers import (
    handle_ai_runtime_error as _handle_ai_runtime_error_impl,
)
from survey_submitter.core.engine.runtime_error_handlers import handle_submission_verification_error
from survey_submitter.core.engine.runtime_error_handlers import (
    handle_survey_provider_unavailable_error,
)

if TYPE_CHECKING:
    from survey_submitter.core.engine.async_events import AsyncRunContext, ThreadEventProxy
    from survey_submitter.core.engine.async_proxy_session import AsyncProxySession
    from survey_submitter.core.engine.runtime_control_port import RuntimeControlPort
    from survey_submitter.core.engine.run_stop_policy import RunStopPolicy
    from survey_submitter.core.task import ExecutionConfig, ExecutionState


class _SlotErrorHandlerMixin:
    """Error handling strategies for slot-level failures."""

    # Attributes provided by the host class (AsyncSlotRunner)
    stop_policy: RunStopPolicy
    config: ExecutionConfig
    stop_proxy: ThreadEventProxy
    slot_label: str
    run_context: AsyncRunContext
    state: ExecutionState
    runtime_bridge: RuntimeControlPort | None
    proxy_session: AsyncProxySession

    def _handle_proxy_unavailable(self, *, status_text: str, log_message: str) -> bool:
        threshold_getter = getattr(self.stop_policy, "proxy_unavailable_threshold", None)
        threshold_value = threshold_getter() if callable(threshold_getter) else None
        threshold_override = (
            int(cast(int, threshold_value))
            if threshold_value is not None
            else max(1, int(self.config.fail_threshold or 1), int(self.config.num_threads or 1))
        )
        stopped = self.stop_policy.record_failure(
            self.stop_proxy,
            thread_name=self.slot_label,
            failure_reason=FailureReason.PROXY_UNAVAILABLE,
            status_text=status_text,
            log_message=log_message,
            threshold_override=threshold_override,
            terminal_stop_category="proxy_unavailable_threshold",
            consume_reverse_fill_attempt=False,
        )
        if stopped:
            self.run_context.stop_event.set()
            return True
        if self.config.random_proxy_ip_enabled and _record_bad_proxy_and_maybe_pause(
            self.state, self.runtime_bridge
        ):
            return True
        return False

    async def _handle_ai_runtime_error(self, exc: AIRuntimeError) -> bool:
        return _handle_ai_runtime_error_impl(
            exc,
            self.stop_proxy,
            thread_name=self.slot_label,
            stop_policy=self.stop_policy,
            state=self.state,
        )

    async def _handle_submission_verification_error(
        self, exc: SubmissionVerificationRequiredError
    ) -> bool:
        if self.config.random_proxy_ip_enabled and self.proxy_session.proxy_address:
            try:
                _mark_proxy_temporarily_bad(self.state, self.proxy_session.proxy_address)
            except Exception:
                logging.debug("标记风控代理失败", exc_info=True)
            stopped = self.stop_policy.record_failure(
                self.stop_proxy,
                thread_name=self.slot_label,
                failure_reason=FailureReason.SUBMISSION_VERIFICATION_REQUIRED,
                status_text="触发验证，换IP",
                log_message=f"当前随机 IP 触发问卷星智能验证，本轮丢弃并更换 IP：{exc}",
                terminal_stop_category="submission_verification_threshold",
                force_stop_when_threshold_reached=True,
                consume_reverse_fill_attempt=False,
            )
            if stopped:
                self.run_context.stop_event.set()
            return bool(stopped)
        return handle_submission_verification_error(
            exc,
            self.stop_proxy,
            thread_name=self.slot_label,
            state=self.state,
        )

    async def _handle_survey_provider_unavailable_error(
        self, exc: SurveyProviderUnavailableAtRuntimeError
    ) -> bool:
        return handle_survey_provider_unavailable_error(
            exc,
            self.stop_proxy,
            thread_name=self.slot_label,
            state=self.state,
        )

    def _handle_http_transport_error(self, exc: BaseException) -> bool:
        if self.proxy_session.proxy_address:
            try:
                _discard_unresponsive_proxy(self.state, self.proxy_session.proxy_address)
            except Exception:
                logging.debug("废弃 HTTP 连接失败代理失败", exc_info=True)
        return self._handle_proxy_unavailable(
            status_text="代理连接失败" if self.proxy_session.proxy_address else "网络请求失败",
            log_message=f"HTTP 请求失败，本轮按失败处理：{exc}",
        )
