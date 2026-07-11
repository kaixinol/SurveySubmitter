from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from survey_submitter.core.ai.runtime import AIRuntimeError
from survey_submitter.core.engine.failure_reason import FailureReason
from survey_submitter.network.session_policy import (
    acquire_submit_proxy,
    release_submit_proxy,
    SubmitProxyUnavailableError,
)
from survey_submitter.providers.errors import (
    SubmissionVerificationRequiredError,
    SurveyProviderUnavailableAtRuntimeError,
)
import survey_submitter.network.http as http_client

if TYPE_CHECKING:
    from survey_submitter.core.engine.async_events import AsyncRunContext, ThreadEventProxy
    from survey_submitter.core.engine.async_http_submitter import AsyncHttpSubmitter
    from survey_submitter.core.engine.async_proxy_session import AsyncProxySession
    from survey_submitter.core.engine.async_scheduler import AsyncScheduler
    from survey_submitter.core.engine.run_stop_policy import RunStopPolicy
    from survey_submitter.core.task import ExecutionConfig, ExecutionState


@dataclass(frozen=True)
class _RoundOutcome:
    dispatch_delay: float = 0.0
    requeue: bool = True
    stop: bool = False


class _HttpRuntimeMixin:
    """HTTP runtime execution loop and related helpers for a slot runner."""

    # Attributes provided by the host class (AsyncSlotRunner) or sibling mixin
    http_submitter: AsyncHttpSubmitter
    slot_label: str
    stop_policy: RunStopPolicy
    stop_proxy: ThreadEventProxy
    state: ExecutionState
    run_context: AsyncRunContext
    scheduler: AsyncScheduler
    proxy_session: AsyncProxySession
    config: ExecutionConfig
    _update_status: Any
    _should_stop_loop: Any
    _release_session_proxy: Any
    _resolve_finished_status_text: Any
    _release_round_resources: Any
    _update_http_step: Any
    _prepare_round_context: Any
    _select_session_proxy_and_ua: Any
    _resolve_dispatch_delay_seconds: Any
    _update_step: Any
    _handle_proxy_unavailable: Any
    _handle_ai_runtime_error: Any
    _handle_submission_verification_error: Any
    _handle_survey_provider_unavailable_error: Any
    _handle_http_transport_error: Any

    def _uses_http_runtime(self) -> bool:
        return self.http_submitter.uses_http_runtime()

    def _resolve_http_runtime_block_reason(self) -> str:
        return self.http_submitter.resolve_block_reason()

    def _block_http_runtime(self, reason: str) -> None:
        message = str(reason or "").strip() or "当前问卷不支持纯 HTTP 提交"
        logging.error("会话[%s]已阻止纯 HTTP 提交：%s", self.slot_label, message)
        self._update_status("纯 HTTP 不支持", running=False)
        self.stop_policy.record_failure(
            self.stop_proxy,
            thread_name=self.slot_label,
            failure_reason=FailureReason.FILL_FAILED,
            status_text="纯 HTTP 不支持",
            log_message=message,
            terminal_stop_category="http_runtime_only",
            force_stop_when_threshold_reached=True,
            consume_reverse_fill_attempt=False,
        )
        self.state.mark_terminal_stop(
            "http_runtime_only",
            failure_reason=FailureReason.FILL_FAILED.value,
            message=message,
        )
        self.run_context.stop_event.set()

    def _mark_http_submit_success(self) -> bool:
        return self.stop_policy.record_success(self.stop_proxy, thread_name=self.slot_label)

    async def _run_http_runtime(self) -> None:
        if self._exit_http_runtime_if_blocked():
            return

        self._update_status("HTTP 会话启动", running=True)
        while True:
            if await self._should_stop_loop():
                break
            token_id = await self.scheduler.acquire()
            if token_id is None:
                break
            outcome = _RoundOutcome()
            try:
                outcome = await self._run_http_round_attempt()
                if outcome.stop:
                    break
                if not outcome.requeue and not outcome.stop:
                    break
            finally:
                if self.proxy_session.proxy_address:
                    release_submit_proxy(
                        self.state, self.slot_label, self.proxy_session.proxy_address
                    )
                self._release_session_proxy()
                await self.scheduler.release(
                    int(token_id),
                    requeue=bool(outcome.requeue and not self.run_context.stop_requested()),
                    delay_seconds=outcome.dispatch_delay,
                )
        self._finalize_http_runtime()

    # ------------------------------------------------------------------
    # Helpers extracted from _run_http_runtime
    # ------------------------------------------------------------------

    def _exit_http_runtime_if_blocked(self) -> bool:
        """If the HTTP runtime is blocked, perform cleanup and return *True*."""
        block_reason = self._resolve_http_runtime_block_reason()
        if not block_reason:
            return False
        self._block_http_runtime(block_reason)
        try:
            self.state.release_reverse_fill_sample(self.slot_label, requeue=True)
            self.state.mark_thread_finished(
                self.slot_label, status_text=self._resolve_finished_status_text()
            )
        except Exception:
            logging.debug("阻止纯 HTTP 提交后的收尾状态更新失败", exc_info=True)
        return True

    def _finalize_http_runtime(self) -> None:
        """Post-loop cleanup: release reverse-fill sample and mark thread finished."""
        try:
            self.state.release_reverse_fill_sample(self.slot_label, requeue=True)
            self.state.mark_thread_finished(
                self.slot_label, status_text=self._resolve_finished_status_text()
            )
        except Exception:
            logging.debug("HTTP slot 收尾状态更新失败", exc_info=True)

    async def _run_http_round_attempt(self) -> _RoundOutcome:
        """Execute one HTTP submission round.

        Returns a :class:`_RoundOutcome` that tells the coordinator whether to
        requeue the scheduler token, apply a dispatch delay, or stop the loop.
        All per-round resource cleanup (round resources) is handled here; the
        coordinator handles proxy / scheduler-token release in its ``finally``.
        """
        try:
            return await self._http_round_try()
        except SubmitProxyUnavailableError as exc:
            self._release_round_resources(requeue_reverse_fill=True)
            if self._handle_proxy_unavailable(
                status_text="代理获取失败",
                log_message=f"提交前未获取到随机 IP，本轮跳过提交：{exc}",
            ):
                return _RoundOutcome(requeue=False, stop=True)
        except AIRuntimeError as exc:
            if await self._handle_ai_runtime_error(exc):
                return _RoundOutcome(requeue=False, stop=True)
            self._release_round_resources(requeue_reverse_fill=True)
        except SubmissionVerificationRequiredError as exc:
            if await self._handle_submission_verification_error(exc):
                return _RoundOutcome(requeue=False, stop=True)
            self._release_round_resources(requeue_reverse_fill=True)
        except SurveyProviderUnavailableAtRuntimeError as exc:
            if await self._handle_survey_provider_unavailable_error(exc):
                return _RoundOutcome(requeue=False, stop=True)
            self._release_round_resources(requeue_reverse_fill=True)
        except (http_client.TransportError,) as exc:
            if self._handle_http_transport_error(exc):
                return _RoundOutcome(requeue=False, stop=True)
            self._release_round_resources(requeue_reverse_fill=True)
        except Exception as exc:
            if self.run_context.stop_requested():
                return _RoundOutcome(requeue=False, stop=True)
            logging.exception("HTTP 会话[%s]运行异常", self.slot_label)
            if self.stop_policy.record_failure(
                self.stop_proxy,
                thread_name=self.slot_label,
                failure_reason=FailureReason.FILL_FAILED,
                log_message=f"HTTP 提交失败，本轮按失败处理：{exc}",
                consume_reverse_fill_attempt=False,
            ):
                return _RoundOutcome(requeue=False, stop=True)
            self._release_round_resources(requeue_reverse_fill=True)
        return _RoundOutcome()

    async def _http_round_try(self) -> _RoundOutcome:
        """Happy-path body for one HTTP round (inside the ``try`` block)."""
        await self._update_http_step("准备请求")
        if not await self._prepare_round_context():
            return _RoundOutcome(requeue=False)

        _proxy_address, ua_value = await self._select_session_proxy_and_ua()
        ua_profile = self.proxy_session.user_agent_profile
        if self.run_context.stop_requested():
            return _RoundOutcome(requeue=False)

        async def submit_proxy_lease_factory():
            if self.config.random_proxy_ip_enabled:
                await self._update_http_step("获取提交代理")
            submit_proxy = await acquire_submit_proxy(
                self.state,
                self.slot_label,
                stop_signal=self.stop_proxy,
                wait=bool(self.config.random_proxy_ip_enabled),
            )
            self.proxy_session.set_current_submit_proxy(
                submit_proxy.address, provider=submit_proxy.provider
            )
            return submit_proxy

        finished = await self.http_submitter.submit(
            stop_signal=self.stop_proxy,
            proxy_address=None,
            user_agent=ua_value,
            user_agent_profile=ua_profile,
            submit_proxy_lease_factory=submit_proxy_lease_factory,
        )
        if self.run_context.stop_requested() or not finished:
            self._release_round_resources(requeue_reverse_fill=True)
            if self.run_context.stop_requested():
                return _RoundOutcome(requeue=False, stop=True)
            return _RoundOutcome()

        should_stop = self._mark_http_submit_success()
        if should_stop:
            return _RoundOutcome(requeue=False, stop=True)
        dispatch_delay_seconds = self._resolve_dispatch_delay_seconds()
        if dispatch_delay_seconds > 0:
            self._update_step("等待提交间隔")
        return _RoundOutcome(dispatch_delay=dispatch_delay_seconds)
