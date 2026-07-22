from __future__ import annotations

import asyncio
from loguru import logger
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Awaitable, TypeVar, cast

from survey_submitter.core.ai.runtime import AIRuntimeError, is_ai_timeout_runtime_error
from survey_submitter.core.engine.async_events import AsyncRunContext, ThreadEventProxy
from survey_submitter.core.engine.async_http_submitter import AsyncHttpSubmitter
from survey_submitter.core.engine.async_proxy_session import AsyncProxySession
from survey_submitter.core.engine.async_scheduler import AsyncScheduler
from survey_submitter.core.engine.failure_reason import FailureReason
from survey_submitter.core.engine.run_stop_policy import RunStopPolicy
from survey_submitter.core.engine.stop_signal import StopSignalLike
from survey_submitter.core.task import ExecutionConfig, ExecutionState
from survey_submitter.network.session_policy import (
    SubmitProxyUnavailableError,
    _discard_unresponsive_proxy,
    _mark_proxy_temporarily_bad,
    _record_bad_proxy_and_maybe_pause,
    acquire_submit_proxy,
    release_submit_proxy,
)
from survey_submitter.providers.errors import (
    SubmissionVerificationRequiredError,
    SurveyProviderUnavailableAtRuntimeError,
)
from survey_submitter.providers.http_progress import update_http_submit_step
import survey_submitter.network.http as http_client

_T = TypeVar("_T")

AI_FILL_FAIL_THRESHOLD = 5
_VERIFICATION_STOP_CATEGORY = "submission_verification"
_PROVIDER_UNAVAILABLE_STOP_CATEGORY = "survey_provider_unavailable"


@dataclass(frozen=True)
class _RoundOutcome:
    dispatch_delay: float = 0.0
    requeue: bool = True
    stop: bool = False


def _safe_state_operation(operation: Callable[[], _T], operation_name: str) -> None:
    try:
        operation()
    except (AttributeError, ValueError, RuntimeError) as exc:
        logger.opt(exception=True).debug(f"{operation_name} 失败：{exc}")
    except Exception:
        logger.opt(exception=True).debug(f"{operation_name} 失败")


def _get_session_proxy_address(session: object) -> str | None:
    if session is None:
        return None
    try:
        return cast("str | None", getattr(session, "proxy_address", None))
    except AttributeError:
        return None


def _handle_ai_runtime_error(
    exc: AIRuntimeError,
    stop_signal: StopSignalLike,
    *,
    thread_name: str,
    stop_policy: RunStopPolicy,
    state: ExecutionState,
) -> bool:
    _ = state
    if is_ai_timeout_runtime_error(exc):
        logger.warning(f"AI 调用超时，本轮丢弃并继续下一轮：{exc}")
        status_text = "AI超时"
        log_message = (
            f"AI调用超时，本轮按失败处理；连续达到 {AI_FILL_FAIL_THRESHOLD} 次才停止：{exc}"
        )
    else:
        logger.opt(exception=True).warning(f"AI 填空失败，本轮丢弃并继续下一轮：{exc}")
        status_text = "AI失败"
        log_message = (
            f"AI填空失败，本轮按失败处理；连续达到 {AI_FILL_FAIL_THRESHOLD} 次才停止：{exc}"
        )

    stopped = stop_policy.record_failure(
        stop_signal,
        thread_name=thread_name,
        failure_reason=FailureReason.FILL_FAILED,
        status_text=status_text,
        log_message=log_message,
        threshold_override=AI_FILL_FAIL_THRESHOLD,
        terminal_stop_category="ai_unstable",
        force_stop=True,
        submission_failed=False,
    )
    if stopped:
        logger.opt(exception=True).error(f"AI 连续失败达到阈值，任务停止：{exc}")
    return bool(stopped)


def _handle_verification_error(
    exc: SubmissionVerificationRequiredError,
    stop_signal: StopSignalLike,
    *,
    thread_name: str,
    state: ExecutionState,
) -> bool:
    message = str(exc or "").strip() or "提交触发智能验证，请启用随机 IP 后再试"
    logger.warning(f"会话[{thread_name}]触发提交智能验证：{message}")

    _safe_state_operation(
        lambda: state.end_round(thread_name),
        "智能验证停止时回收轮次样本",
    )
    _safe_state_operation(
        lambda: state.increment_thread_fail(thread_name, status_text="触发智能验证"),
        "智能验证停止时更新线程状态",
    )

    state.mark_terminal_stop(
        _VERIFICATION_STOP_CATEGORY,
        failure_reason=FailureReason.SUBMISSION_VERIFICATION_REQUIRED.value,
        message=message,
    )
    stop_signal.set()
    return True


def _handle_provider_unavailable(
    exc: SurveyProviderUnavailableAtRuntimeError,
    stop_signal: StopSignalLike,
    *,
    thread_name: str,
    state: ExecutionState,
) -> bool:
    message = str(exc or "").strip() or "问卷当前不可填写"
    logger.warning(f"会话[{thread_name}]发现问卷不可继续：{message}")

    _safe_state_operation(
        lambda: state.end_round(thread_name),
        "问卷不可继续时回收轮次样本",
    )
    _safe_state_operation(
        lambda: state.increment_thread_fail(thread_name, status_text="问卷不可填写"),
        "问卷不可继续时更新线程状态",
    )

    state.mark_terminal_stop(
        _PROVIDER_UNAVAILABLE_STOP_CATEGORY,
        failure_reason=FailureReason.SURVEY_PROVIDER_UNAVAILABLE.value,
        message=message,
    )
    stop_signal.set()
    return True


class _AsyncRoundResources:
    def __init__(
        self,
        *,
        config: ExecutionConfig,
        state: ExecutionState,
        slot_label: str,
        stop_requested: Callable[[], bool],
        should_stop_loop: Callable[[], Awaitable[bool]],
        set_stop_requested: Callable[[], None],
        update_status: Callable[..., None],
    ) -> None:
        self.config = config
        self.state = state
        self.slot_label = slot_label
        self.stop_requested = stop_requested
        self.should_stop_loop = should_stop_loop
        self.set_stop_requested = set_stop_requested
        self.update_status = update_status

    async def prepare_round_context(self) -> bool:
        try:
            self.state.reset_pending_distribution(self.slot_label)
        except Exception:
            logger.opt(exception=True).debug("重置本轮比例统计缓存失败")

        while True:
            if self.stop_requested():
                return False
            if await self.should_stop_loop():
                return False

            reverse_fill_sample = self.state.acquire_sample(self.slot_label)

            if reverse_fill_sample.status == "waiting":
                self.update_status("等待反填样本")
                await asyncio.sleep(0.5)
                continue

            if reverse_fill_sample.status == "exhausted":
                message = "反填样本已耗尽，剩余样本不足以完成目标份数"
                self.state.mark_terminal_stop(
                    "reverse_fill_exhausted",
                    failure_reason=FailureReason.FILL_FAILED.value,
                    message=message,
                )
                self.update_status("反填样本不足", running=False)
                self.set_stop_requested()
                return False

            if reverse_fill_sample.status == "acquired" and reverse_fill_sample.sample is not None:
                logger.info(
                    f"会话[{self.slot_label}]已锁定反填样本：数据行={reverse_fill_sample.sample.data_row_number} 工作表行={reverse_fill_sample.sample.worksheet_row_number}"
                )
            return True

    def release_round_resources(self, *, submission_failed: bool = False) -> None:
        try:
            self.state.end_round(
                self.slot_label,
                submission_failed=submission_failed,
            )
        except Exception:
            logger.opt(exception=True).debug("释放轮次资源失败")


class AsyncSlotRunner:
    def __init__(
        self,
        *,
        slot_id: int,
        config: ExecutionConfig,
        state: ExecutionState,
        run_context: AsyncRunContext,
        scheduler: AsyncScheduler,
    ) -> None:
        self.slot_id = max(1, int(slot_id or 1))
        self.slot_label = f"Slot-{self.slot_id}"
        self.config = config
        self.state = state
        self.run_context = run_context
        self.scheduler = scheduler
        self.stop_proxy = ThreadEventProxy(run_context.stop_event, loop=asyncio.get_running_loop())
        self.stop_policy = RunStopPolicy(config, state)
        self.proxy_session = AsyncProxySession(
            config=config,
            state=state,
            slot_label=self.slot_label,
            stop_signal=self.stop_proxy,
            update_step=self._update_step,
        )
        self.round_resources = _AsyncRoundResources(
            config=config,
            state=state,
            slot_label=self.slot_label,
            stop_requested=self.run_context.stop_requested,
            should_stop_loop=self._should_stop_loop,
            set_stop_requested=self.run_context.stop_event.set,
            update_status=self._update_status,
        )
        self.http_submitter = AsyncHttpSubmitter(
            config=config,
            state=state,
            slot_label=self.slot_label,
        )

    # ------------------------------------------------------------------
    # Status / step updates
    # ------------------------------------------------------------------

    def _update_status(self, status_text: str, *, running: bool = True) -> None:
        try:
            self.state.update_thread_status(self.slot_label, status_text, running=running)
        except Exception:
            logger.opt(exception=True).debug(f"更新 slot 状态失败：{status_text}")

    def _update_step(self, status_text: str) -> None:
        try:
            self.state.update_thread_step(
                self.slot_label, 0, 0, status_text=status_text, running=True
            )
        except Exception:
            logger.opt(exception=True).debug(f"更新 slot 步骤失败：{status_text}")

    async def _update_http_step(self, status_text: str) -> None:
        await update_http_submit_step(self.state, self.slot_label, status_text)

    # ------------------------------------------------------------------
    # Loop control
    # ------------------------------------------------------------------

    async def _should_stop_loop(self) -> bool:
        await self.run_context.wait_if_paused()
        if self.run_context.stop_requested():
            return True
        with self.state.lock:
            target_reached = bool(
                self.config.target_num > 0 and self.state.success_count >= self.config.target_num
            )
        if target_reached:
            self.stop_policy.trigger_target_reached_stop(self.stop_proxy)
            return True
        return False

    async def _sleep_or_stop(self, seconds: float) -> bool:
        delay = max(0.0, float(seconds or 0.0))
        if delay <= 0:
            return self.run_context.stop_requested()
        try:
            await asyncio.wait_for(self.run_context.stop_event.wait(), timeout=delay)
            return True
        except asyncio.TimeoutError:
            return self.run_context.stop_requested()

    def _resolve_dispatch_delay_seconds(self) -> float:
        min_wait, max_wait = self.config.submit_interval_range_seconds
        if max_wait <= 0:
            return 0.0
        if max_wait == min_wait:
            return float(min_wait)
        return float(random.uniform(min_wait, max_wait))

    def _resolve_finished_status_text(self) -> str:
        if self.run_context.stop_requested():
            try:
                terminal_category = str(self.state.get_terminal_stop_snapshot()[0] or "").strip()
            except Exception:
                logger.opt(exception=True).debug("获取终端停止快照失败")
                terminal_category = ""
            if terminal_category == "target_reached":
                return "已完成"
        return "已停止"

    # ------------------------------------------------------------------
    # Round resource delegation
    # ------------------------------------------------------------------

    async def _prepare_round_context(self) -> bool:
        return await self.round_resources.prepare_round_context()

    def _release_round_resources(self, *, submission_failed: bool = False) -> None:
        self.round_resources.release_round_resources(submission_failed=submission_failed)

    async def _select_session_proxy_and_ua(self) -> tuple[str | None, str | None]:
        return None, await self.proxy_session.select_user_agent()

    def _release_session_proxy(self) -> None:
        self.proxy_session.release_current_proxy()

    # ------------------------------------------------------------------
    # Error handling (inlined from _SlotErrorHandlerMixin)
    # ------------------------------------------------------------------

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
            submission_failed=False,
        )
        if stopped:
            self.run_context.stop_event.set()
            return True
        if self.config.random_proxy_ip and _record_bad_proxy_and_maybe_pause(
            self.state
        ):
            return True
        return False

    async def _handle_ai_runtime_error(self, exc: AIRuntimeError) -> bool:
        return _handle_ai_runtime_error(
            exc,
            self.stop_proxy,
            thread_name=self.slot_label,
            stop_policy=self.stop_policy,
            state=self.state,
        )

    async def _handle_submission_verification_error(
        self, exc: SubmissionVerificationRequiredError
    ) -> bool:
        if self.config.random_proxy_ip and self.proxy_session.proxy_address:
            try:
                _mark_proxy_temporarily_bad(self.state, self.proxy_session.proxy_address)
            except Exception:
                logger.opt(exception=True).debug("标记风控代理失败")
            stopped = self.stop_policy.record_failure(
                self.stop_proxy,
                thread_name=self.slot_label,
                failure_reason=FailureReason.SUBMISSION_VERIFICATION_REQUIRED,
                status_text="触发验证，换IP",
                log_message=f"当前随机 IP 触发问卷星智能验证，本轮丢弃并更换 IP：{exc}",
                terminal_stop_category="submission_verification_threshold",
                force_stop=True,
                submission_failed=False,
            )
            if stopped:
                self.run_context.stop_event.set()
            return bool(stopped)
        return _handle_verification_error(
            exc,
            self.stop_proxy,
            thread_name=self.slot_label,
            state=self.state,
        )

    async def _handle_survey_provider_unavailable_error(
        self, exc: SurveyProviderUnavailableAtRuntimeError
    ) -> bool:
        return _handle_provider_unavailable(
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
                logger.opt(exception=True).debug("废弃 HTTP 连接失败代理失败")
        return self._handle_proxy_unavailable(
            status_text="代理连接失败" if self.proxy_session.proxy_address else "网络请求失败",
            log_message=f"HTTP 请求失败，本轮按失败处理：{exc}",
        )

    # ------------------------------------------------------------------
    # HTTP runtime (inlined from _HttpRuntimeMixin)
    # ------------------------------------------------------------------

    def _uses_http_runtime(self) -> bool:
        return self.http_submitter.uses_http_runtime()

    def _resolve_http_runtime_block_reason(self) -> str:
        return self.http_submitter.resolve_block_reason()

    def _block_http_runtime(self, reason: str) -> None:
        message = str(reason or "").strip() or "当前问卷不支持纯 HTTP 提交"
        logger.error(f"会话[{self.slot_label}]已阻止纯 HTTP 提交：{message}")
        self._update_status("纯 HTTP 不支持", running=False)
        self.stop_policy.record_failure(
            self.stop_proxy,
            thread_name=self.slot_label,
            failure_reason=FailureReason.FILL_FAILED,
            status_text="纯 HTTP 不支持",
            log_message=message,
            terminal_stop_category="http_runtime_only",
            force_stop=True,
            submission_failed=False,
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

    def _exit_http_runtime_if_blocked(self) -> bool:
        block_reason = self._resolve_http_runtime_block_reason()
        if not block_reason:
            return False
        self._block_http_runtime(block_reason)
        try:
            self.state.end_round(self.slot_label)
            self.state.mark_thread_finished(
                self.slot_label, status_text=self._resolve_finished_status_text()
            )
        except Exception:
            logger.opt(exception=True).debug("阻止纯 HTTP 提交后的收尾状态更新失败")
        return True

    def _finalize_http_runtime(self) -> None:
        try:
            self.state.end_round(self.slot_label)
            self.state.mark_thread_finished(
                self.slot_label, status_text=self._resolve_finished_status_text()
            )
        except Exception:
            logger.opt(exception=True).debug("HTTP slot 收尾状态更新失败")

    async def _run_http_round_attempt(self) -> _RoundOutcome:
        try:
            return await self._http_round_try()
        except SubmitProxyUnavailableError as exc:
            self._release_round_resources()
            if self._handle_proxy_unavailable(
                status_text="代理获取失败",
                log_message=f"提交前未获取到随机 IP，本轮跳过提交：{exc}",
            ):
                return _RoundOutcome(requeue=False, stop=True)
        except AIRuntimeError as exc:
            if await self._handle_ai_runtime_error(exc):
                return _RoundOutcome(requeue=False, stop=True)
            self._release_round_resources()
        except SubmissionVerificationRequiredError as exc:
            if await self._handle_submission_verification_error(exc):
                return _RoundOutcome(requeue=False, stop=True)
            self._release_round_resources()
        except SurveyProviderUnavailableAtRuntimeError as exc:
            if await self._handle_survey_provider_unavailable_error(exc):
                return _RoundOutcome(requeue=False, stop=True)
            self._release_round_resources()
        except (http_client.TransportError,) as exc:
            if self._handle_http_transport_error(exc):
                return _RoundOutcome(requeue=False, stop=True)
            self._release_round_resources()
        except Exception as exc:
            if self.run_context.stop_requested():
                return _RoundOutcome(requeue=False, stop=True)
            logger.exception(f"HTTP 会话[{self.slot_label}]运行异常")
            if self.stop_policy.record_failure(
                self.stop_proxy,
                thread_name=self.slot_label,
                failure_reason=FailureReason.FILL_FAILED,
                log_message=f"HTTP 提交失败，本轮按失败处理：{exc}",
                submission_failed=False,
            ):
                return _RoundOutcome(requeue=False, stop=True)
            self._release_round_resources()
        return _RoundOutcome()

    async def _http_round_try(self) -> _RoundOutcome:
        await self._update_http_step("准备请求")
        if not await self._prepare_round_context():
            return _RoundOutcome(requeue=False)

        _proxy_address, ua_value = await self._select_session_proxy_and_ua()
        ua_profile = self.proxy_session.user_agent_profile
        if self.run_context.stop_requested():
            return _RoundOutcome(requeue=False)

        async def submit_proxy_lease_factory():
            if self.config.random_proxy_ip:
                await self._update_http_step("获取提交代理")
            submit_proxy = await acquire_submit_proxy(
                self.state,
                self.slot_label,
                stop_signal=self.stop_proxy,
                wait=bool(self.config.random_proxy_ip),
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
            self._release_round_resources()
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

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        if not self._uses_http_runtime():
            self._block_http_runtime(self._resolve_http_runtime_block_reason())
            return
        await self._run_http_runtime()


__all__ = ["AsyncSlotRunner"]
