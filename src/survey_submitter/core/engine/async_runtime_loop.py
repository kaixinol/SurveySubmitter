from __future__ import annotations

import asyncio
import logging
import random

from survey_submitter.core.engine.async_events import AsyncRunContext, ThreadEventProxy
from survey_submitter.core.engine.async_http_submitter import AsyncHttpSubmitter
from survey_submitter.core.engine.async_proxy_session import AsyncProxySession
from survey_submitter.core.engine.async_round_resources import AsyncRoundResources
from survey_submitter.core.engine.async_scheduler import AsyncScheduler
from survey_submitter.core.engine.runtime_control_port import RuntimeControlPort
from survey_submitter.core.engine.run_stop_policy import RunStopPolicy
from survey_submitter.core.engine.runtime_slot_error_mixin import _SlotErrorHandlerMixin
from survey_submitter.core.engine.runtime_slot_http_mixin import _HttpRuntimeMixin, _RoundOutcome
from survey_submitter.core.task import ExecutionConfig, ExecutionState
import survey_submitter.network.http as http_client
from survey_submitter.network.session_policy import (
    _discard_unresponsive_proxy,
    _mark_proxy_temporarily_bad,
    _record_bad_proxy_and_maybe_pause,
    SubmitProxyUnavailableError,
    acquire_submit_proxy,
    release_submit_proxy,
)
from survey_submitter.providers.http_progress import update_http_submit_step


class AsyncSlotRunner(_SlotErrorHandlerMixin, _HttpRuntimeMixin):

    def __init__(
        self,
        *,
        slot_id: int,
        config: ExecutionConfig,
        state: ExecutionState,
        run_context: AsyncRunContext,
        scheduler: AsyncScheduler,
        runtime_bridge: RuntimeControlPort | None = None,
    ) -> None:
        self.slot_id = max(1, int(slot_id or 1))
        self.slot_label = f"Slot-{self.slot_id}"
        self.config = config
        self.state = state
        self.run_context = run_context
        self.scheduler = scheduler
        self.runtime_bridge = runtime_bridge
        self.stop_proxy = ThreadEventProxy(run_context.stop_event, loop=asyncio.get_running_loop())
        self.stop_policy = RunStopPolicy(config, state, runtime_bridge)
        self.proxy_session = AsyncProxySession(
            config=config,
            state=state,
            slot_label=self.slot_label,
            stop_signal=self.stop_proxy,
            runtime_bridge=runtime_bridge,
            update_step=self._update_step,
        )
        self.round_resources = AsyncRoundResources(
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

    def _update_status(self, status_text: str, *, running: bool = True) -> None:
        try:
            self.state.update_thread_status(self.slot_label, status_text, running=running)
        except Exception:
            logging.debug("更新 slot 状态失败：%s", status_text, exc_info=True)

    def _update_step(self, status_text: str) -> None:
        try:
            self.state.update_thread_step(self.slot_label, 0, 0, status_text=status_text, running=True)
        except Exception:
            logging.debug("更新 slot 步骤失败：%s", status_text, exc_info=True)

    async def _update_http_step(self, status_text: str) -> None:
        await update_http_submit_step(self.state, self.slot_label, status_text)

    async def _should_stop_loop(self) -> bool:
        await self.run_context.wait_if_paused()
        if self.run_context.stop_requested():
            return True
        with self.state.lock:
            target_reached = bool(self.config.target_num > 0 and self.state.cur_num >= self.config.target_num)
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
                logging.debug("获取终端停止快照失败", exc_info=True)
                terminal_category = ""
            if terminal_category == "target_reached":
                return "已完成"
        return "已停止"

    async def _prepare_round_context(self) -> bool:
        return await self.round_resources.prepare_round_context()

    def _release_round_resources(self, *, requeue_reverse_fill: bool) -> None:
        self.round_resources.release_round_resources(requeue_reverse_fill=requeue_reverse_fill)

    async def _select_session_proxy_and_ua(self) -> tuple[str | None, str | None]:
        return None, await self.proxy_session.select_user_agent()

    def _release_session_proxy(self) -> None:
        self.proxy_session.release_current_proxy()

    async def run(self) -> None:
        if not self._uses_http_runtime():
            self._block_http_runtime(self._resolve_http_runtime_block_reason())
            return
        await self._run_http_runtime()


__all__ = ["AsyncSlotRunner"]
