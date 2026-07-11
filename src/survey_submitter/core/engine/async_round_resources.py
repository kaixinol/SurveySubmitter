from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from survey_submitter.core.engine.failure_reason import FailureReason
from survey_submitter.core.task import ExecutionConfig, ExecutionState


class AsyncRoundResources:
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
            logging.debug("重置本轮比例统计缓存失败", exc_info=True)

        while True:
            if self.stop_requested():
                return False
            if await self.should_stop_loop():
                return False

            reverse_fill_sample = self.state.acquire_reverse_fill_sample(self.slot_label)

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
                logging.info(
                    "会话[%s]已锁定反填样本：数据行=%s 工作表行=%s",
                    self.slot_label,
                    reverse_fill_sample.sample.data_row_number,
                    reverse_fill_sample.sample.worksheet_row_number,
                )
            return True

    def release_round_resources(self, *, submission_failed: bool = False) -> None:
        try:
            self.state.end_round(
                self.slot_label,
                submission_failed=submission_failed,
            )
        except Exception:
            logging.debug("释放轮次资源失败", exc_info=True)


__all__ = [
    "AsyncRoundResources",
]
