from __future__ import annotations

import asyncio
import threading

from survey_submitter.core.ai.runtime import AIRuntimeError
from survey_submitter.core.engine import async_runtime_loop
from survey_submitter.core.engine.async_events import AsyncRunContext
from survey_submitter.core.engine.failure_reason import FailureReason
from survey_submitter.core.task import ExecutionConfig, ExecutionState
from survey_submitter.providers.errors import SubmissionVerificationRequiredError


class _StubScheduler:
    async def acquire(self) -> int:
        return 1

    async def release(self, token_id: object, **kwargs: object) -> None:
        return None


def _build_runner(
    *, config: ExecutionConfig, state: ExecutionState
) -> tuple[async_runtime_loop.AsyncSlotRunner, AsyncRunContext]:
    run_context = AsyncRunContext(
        state=state,
        stop_event=asyncio.Event(),
        pause_event=asyncio.Event(),
    )
    runner = async_runtime_loop.AsyncSlotRunner(
        slot_id=1,
        config=config,
        state=state,
        run_context=run_context,
        scheduler=_StubScheduler(),  # ty:ignore[invalid-argument-type]
    )
    return runner, run_context


class RuntimeErrorHandlerTests:
    async def test_ai_runtime_error_keeps_running_until_five_failures(self) -> None:
        config = ExecutionConfig(control={'fail_threshold': 5, 'stop_on_fail': True})
        state = ExecutionState(config=config)
        runner, run_context = _build_runner(config=config, state=state)

        stopped = False
        for _ in range(async_runtime_loop.AI_FILL_FAIL_THRESHOLD - 1):
            stopped = runner._handle_ai_runtime_error(AIRuntimeError("AI 调用失败：临时故障"))

        assert not stopped
        assert not run_context.stop_event.is_set()
        assert state.consecutive_fail_count == async_runtime_loop.AI_FILL_FAIL_THRESHOLD - 1
        assert state.get_terminal_stop_snapshot()[0] == ""

    async def test_ai_runtime_error_stops_on_fifth_failure(self) -> None:
        config = ExecutionConfig(control={'fail_threshold': 5, 'stop_on_fail': True})
        state = ExecutionState(
            config=config, consecutive_fail_count=async_runtime_loop.AI_FILL_FAIL_THRESHOLD - 1
        )
        runner, run_context = _build_runner(config=config, state=state)

        stopped = runner._handle_ai_runtime_error(AIRuntimeError("AI 调用失败：临时故障"))
        await asyncio.sleep(0)  # ThreadEventProxy 经 call_soon_threadsafe 置位停止事件

        assert stopped
        assert run_context.stop_event.is_set()
        assert state.get_terminal_stop_snapshot()[0] == "ai_unstable"
        assert state.get_terminal_stop_snapshot()[1] == FailureReason.FILL_FAILED.value

    def test_submission_verification_error_stops_immediately(self) -> None:
        config = ExecutionConfig(control={'fail_threshold': 5, 'stop_on_fail': True})
        state = ExecutionState(config=config)
        stop_signal = threading.Event()

        stopped = async_runtime_loop._handle_verification_error(
            SubmissionVerificationRequiredError(
                "问卷星触发智能验证，当前链路已停止。请启用随机 IP 后再提交。"
            ),
            stop_signal,
            thread_name="Worker-1",
            state=state,
        )

        assert stopped
        assert stop_signal.is_set()
        assert state.get_terminal_stop_snapshot()[0] == "submission_verification"
        assert (
            state.get_terminal_stop_snapshot()[1]
            == FailureReason.SUBMISSION_VERIFICATION_REQUIRED.value
        )
        assert "启用随机 IP" in state.get_terminal_stop_snapshot()[2]
