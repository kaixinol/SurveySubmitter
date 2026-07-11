from __future__ import annotations

import logging
import math
import time
from typing import Callable, TypeVar

from survey_submitter.core.engine.failure_reason import FailureReason
from survey_submitter.core.engine.runtime_control_port import (
    RuntimeControlPort,
)
from survey_submitter.core.engine.runtime_control_port import (
    on_random_ip_submission as trigger_random_ip_submission,
)
from survey_submitter.core.engine.runtime_control_port import (
    wait_if_paused as runtime_wait_if_paused,
)
from survey_submitter.core.engine.stop_signal import StopSignalLike
from survey_submitter.core.task import ExecutionConfig, ExecutionState

T = TypeVar("T")


def _safe_cleanup_call(
    operation: Callable[[], T],
    operation_name: str,
) -> T | None:
    """Execute a cleanup operation safely, logging any failures without raising.

    Used for cleanup/update operations that should not interrupt the main flow.
    """
    try:
        return operation()
    except (AttributeError, ValueError, TypeError, RuntimeError) as exc:
        logging.debug("%s 失败：%s", operation_name, exc, exc_info=True)
        return None
    except Exception:
        # Catch-all for unexpected errors in cleanup operations
        logging.debug("%s 失败", operation_name, exc_info=True)
        return None


class RunStopPolicy:
    def __init__(
        self,
        config: ExecutionConfig,
        state: ExecutionState,
        runtime_bridge: RuntimeControlPort | None = None,
    ):
        self.config = config
        self.state = state
        self.runtime_bridge = runtime_bridge

    def wait_if_paused(self, stop_signal: StopSignalLike | None) -> None:
        _safe_cleanup_call(
            lambda: runtime_wait_if_paused(self.runtime_bridge, stop_signal), "暂停等待"
        )

    def failure_threshold(self) -> int:
        base_threshold = max(1, int(self.config.fail_threshold or 1))
        num_threads = max(1, int(self.config.num_threads or 1))
        if num_threads > 10:
            return max(base_threshold, int(math.ceil(num_threads / 2.0)))
        return base_threshold

    def proxy_unavailable_threshold(self) -> int:
        base_threshold = self.failure_threshold()
        if not bool(self.config.random_proxy_ip_enabled):
            return base_threshold
        return max(base_threshold, int(self.config.num_threads or 1))

    def record_failure(
        self,
        stop_signal: StopSignalLike | None,
        thread_name: str | None = None,
        *,
        failure_reason: FailureReason = FailureReason.FILL_FAILED,
        status_text: str = "失败重试",
        log_message: str = "",
        threshold_override: int | None = None,
        terminal_stop_category: str = "fail_threshold",
        force_stop_when_threshold_reached: bool = False,
        submission_failed: bool = True,
    ) -> bool:
        stop_threshold = max(1, int(threshold_override or self.failure_threshold()))
        is_proxy_unavailable = failure_reason == FailureReason.PROXY_UNAVAILABLE
        with self.state.lock:
            if is_proxy_unavailable:
                self.state.proxy_unavailable_fail_count = (
                    max(0, int(self.state.proxy_unavailable_fail_count or 0)) + 1
                )
                consecutive_failures = int(self.state.proxy_unavailable_fail_count or 0)
            else:
                self.state.cur_fail += 1
                consecutive_failures = int(self.state.cur_fail or 0)
            message = str(log_message or "").strip()
            if message:
                logging.warning("%s", message)
            threshold_enabled = bool(
                self.config.stop_on_fail_enabled or force_stop_when_threshold_reached
            )
            if threshold_enabled:
                logging.warning(
                    "已连续失败%s次，连续失败达到%s次将强制停止",
                    consecutive_failures,
                    stop_threshold,
                )
            else:
                logging.warning("已连续失败%s次（失败止损已关闭）", consecutive_failures)
        if thread_name:
            _safe_cleanup_call(
                lambda: self.state.end_round(
                    thread_name,
                    submission_failed=submission_failed,
                ),
                "回收轮次资源",
            )
            _safe_cleanup_call(
                lambda: self.state.increment_thread_fail(thread_name, status_text=status_text),
                "更新线程失败计数",
            )
        if self.state.is_round_target_unreachable():
            message = "反填样本已耗尽，剩余样本不足以完成目标份数"
            logging.critical("%s", message)
            self.state.mark_terminal_stop(
                "reverse_fill_exhausted",
                failure_reason=FailureReason.FILL_FAILED.value,
                message=message,
            )
            if stop_signal:
                stop_signal.set()
            return True
        threshold_enabled = bool(
            self.config.stop_on_fail_enabled or force_stop_when_threshold_reached
        )
        if threshold_enabled and consecutive_failures >= stop_threshold:
            logging.critical("连续失败次数过多，强制停止，请检查配置是否正确")
            self.state.mark_terminal_stop(
                terminal_stop_category,
                failure_reason=failure_reason.value,
                message=message or status_text,
            )
            if stop_signal:
                stop_signal.set()
            return True
        return False

    def record_success(
        self,
        stop_signal: StopSignalLike,
        thread_name: str | None = None,
        *,
        status_text: str = "提交成功",
        terminal_message: str = "目标份数已达成",
    ) -> bool:
        should_handle_random_ip = False
        trigger_target_stop = False
        should_break = False
        record_thread_success = False
        previous_consecutive_failures = 0

        with self.state.lock:
            if self.config.target_num <= 0 or self.state.cur_num < self.config.target_num:
                previous_consecutive_failures = int(self.state.cur_fail or 0)
                self.state.cur_num += 1
                self.state.cur_fail = 0
                self.state.proxy_unavailable_fail_count = 0
                record_thread_success = True
                logging.info(
                    "[OK] 已填写%s份 - 连续失败%s次 - %s",
                    self.state.cur_num,
                    self.state.cur_fail,
                    time.strftime("%H:%M:%S", time.localtime(time.time())),
                )
                if previous_consecutive_failures > 0:
                    logging.info(
                        "提交成功，连续失败计数已清零（重置前=%s）", previous_consecutive_failures
                    )
                should_handle_random_ip = self.config.random_proxy_ip_enabled
                if self.config.target_num > 0 and self.state.cur_num >= self.config.target_num:
                    trigger_target_stop = True
            else:
                should_break = True

        if record_thread_success and thread_name:
            _safe_cleanup_call(
                lambda: self.state.complete_round(thread_name), "核销轮次样本"
            )
            _safe_cleanup_call(
                lambda: self.state.commit_pending_distribution(thread_name), "写入比例统计"
            )
            _safe_cleanup_call(
                lambda: self.state.increment_thread_success(thread_name, status_text=status_text),
                "更新线程成功计数",
            )
        if should_break:
            stop_signal.set()
        if trigger_target_stop:
            self.trigger_target_reached_stop(stop_signal, message=terminal_message)
        if should_handle_random_ip:
            _safe_cleanup_call(
                lambda: trigger_random_ip_submission(self.runtime_bridge, stop_signal), "刷新随机IP"
            )
        return should_break or trigger_target_stop

    def trigger_target_reached_stop(
        self,
        stop_signal: StopSignalLike | None,
        *,
        message: str = "目标份数已达成",
    ) -> None:
        with self.state._target_reached_stop_lock:
            if self.state._target_reached_stop_triggered:
                if stop_signal:
                    stop_signal.set()
                return
            self.state._target_reached_stop_triggered = True
        self.state.mark_terminal_stop("target_reached", message=message)
        if stop_signal:
            stop_signal.set()


__all__ = ["RunStopPolicy"]
