from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from survey_submitter.core.ai.runtime import AIRuntimeError, is_ai_timeout_runtime_error
from survey_submitter.core.engine.failure_reason import FailureReason
from survey_submitter.core.engine.run_stop_policy import RunStopPolicy
from survey_submitter.core.engine.stop_signal import StopSignalLike
from survey_submitter.core.task import ExecutionConfig, ExecutionState
from survey_submitter.providers.errors import (
    SubmissionVerificationRequiredError,
    SurveyProviderUnavailableAtRuntimeError,
)

T = TypeVar("T")

AI_FILL_FAIL_THRESHOLD = 5
SUBMISSION_VERIFICATION_STOP_CATEGORY = "submission_verification"
SURVEY_PROVIDER_UNAVAILABLE_STOP_CATEGORY = "survey_provider_unavailable"


def _safe_state_operation(
    operation: Callable[[], T],
    operation_name: str,
) -> None:
    """Execute a state operation safely, logging failures without raising."""
    try:
        operation()
    except (AttributeError, ValueError, RuntimeError) as exc:
        logging.debug("%s 失败：%s", operation_name, exc, exc_info=True)
    except Exception:
        logging.debug("%s 失败", operation_name, exc_info=True)


def _get_session_proxy_address(session: object) -> str | None:
    """Safely get proxy address from session object."""
    if session is None:
        return None
    try:
        return getattr(session, "proxy_address", None)  # type: ignore[return-value]
    except AttributeError:
        return None


def handle_ai_runtime_error(
    exc: AIRuntimeError,
    stop_signal: StopSignalLike,
    *,
    thread_name: str,
    stop_policy: RunStopPolicy,
    state: ExecutionState,
) -> bool:
    _ = state
    if is_ai_timeout_runtime_error(exc):
        logging.warning("AI 调用超时，本轮丢弃并继续下一轮：%s", exc)
        status_text = "AI超时"
        log_message = f"AI调用超时，本轮按失败处理；连续达到 {AI_FILL_FAIL_THRESHOLD} 次才停止：{exc}"
    else:
        logging.warning("AI 填空失败，本轮丢弃并继续下一轮：%s", exc, exc_info=True)
        status_text = "AI失败"
        log_message = f"AI填空失败，本轮按失败处理；连续达到 {AI_FILL_FAIL_THRESHOLD} 次才停止：{exc}"

    stopped = stop_policy.record_failure(
        stop_signal,
        thread_name=thread_name,
        failure_reason=FailureReason.FILL_FAILED,
        status_text=status_text,
        log_message=log_message,
        threshold_override=AI_FILL_FAIL_THRESHOLD,
        terminal_stop_category="ai_unstable",
        force_stop_when_threshold_reached=True,
        consume_reverse_fill_attempt=False,
    )
    if stopped:
        logging.error("AI 连续失败达到阈值，任务停止：%s", exc, exc_info=True)
    return bool(stopped)


def handle_proxy_connection_error(
    session: object,
    stop_signal: StopSignalLike,
    *,
    thread_name: str,
    state: ExecutionState,
    config: ExecutionConfig,
    stop_policy: RunStopPolicy,
    update_thread_status: Callable[[str, str], None],
    handle_proxy_unavailable: Callable[..., bool],
    mark_proxy_temporarily_bad: Callable[[ExecutionState, str], None],
) -> bool:
    if stop_signal.is_set():
        return True
    logging.warning("代理连接失败，当前会话将废弃并重新尝试")
    proxy_address = _get_session_proxy_address(session)
    if proxy_address:
        mark_proxy_temporarily_bad(state, proxy_address)
    if config.random_proxy_ip_enabled:
        update_thread_status(thread_name, "代理失效，切换中")
        if handle_proxy_unavailable(
            stop_signal,
            thread_name=thread_name,
            status_text="代理不可用",
            log_message="代理连接失败，本轮按失败处理",
        ):
            return True
        return False
    return stop_policy.record_failure(
        stop_signal,
        thread_name=thread_name,
        failure_reason=FailureReason.PROXY_UNAVAILABLE,
        consume_reverse_fill_attempt=False,
    )


def handle_submission_verification_error(
    exc: SubmissionVerificationRequiredError,
    stop_signal: StopSignalLike,
    *,
    thread_name: str,
    state: ExecutionState,
) -> bool:
    message = str(exc or "").strip() or "提交触发智能验证，请启用随机 IP 后再试"
    logging.warning("会话[%s]触发提交智能验证：%s", thread_name, message)

    _safe_state_operation(
        lambda: state.release_reverse_fill_sample(thread_name, requeue=True),
        "智能验证停止时回收反填样本"
    )
    _safe_state_operation(
        lambda: state.increment_thread_fail(thread_name, status_text="触发智能验证"),
        "智能验证停止时更新线程状态"
    )

    state.mark_terminal_stop(
        SUBMISSION_VERIFICATION_STOP_CATEGORY,
        failure_reason=FailureReason.SUBMISSION_VERIFICATION_REQUIRED.value,
        message=message,
    )
    stop_signal.set()
    return True


def handle_survey_provider_unavailable_error(
    exc: SurveyProviderUnavailableAtRuntimeError,
    stop_signal: StopSignalLike,
    *,
    thread_name: str,
    state: ExecutionState,
) -> bool:
    message = str(exc or "").strip() or "问卷当前不可填写"
    logging.warning("会话[%s]发现问卷不可继续：%s", thread_name, message)

    _safe_state_operation(
        lambda: state.release_reverse_fill_sample(thread_name, requeue=True),
        "问卷不可继续时回收反填样本"
    )
    _safe_state_operation(
        lambda: state.increment_thread_fail(thread_name, status_text="问卷不可填写"),
        "问卷不可继续时更新线程状态"
    )

    state.mark_terminal_stop(
        SURVEY_PROVIDER_UNAVAILABLE_STOP_CATEGORY,
        failure_reason=FailureReason.SURVEY_PROVIDER_UNAVAILABLE.value,
        message=message,
    )
    stop_signal.set()
    return True


__all__ = [
    "AI_FILL_FAIL_THRESHOLD",
    "SUBMISSION_VERIFICATION_STOP_CATEGORY",
    "SURVEY_PROVIDER_UNAVAILABLE_STOP_CATEGORY",
    "handle_ai_runtime_error",
    "handle_proxy_connection_error",
    "handle_submission_verification_error",
    "handle_survey_provider_unavailable_error",
]
