from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Protocol

from survey_submitter.core.reverse_fill import (
    ReverseFillAcquireResult,
    ReverseFillAnswer,
    ReverseFillRuntimeState,
    create_reverse_fill_runtime_state,
)

if TYPE_CHECKING:

    class _ReverseFillRuntimeHost(Protocol):
        lock: threading.Lock
        config: Any
        success_count: int
        reverse_fill_runtime: ReverseFillRuntimeState | None

        def _thread_key(self, thread_name: str | None = None) -> str: ...
        def _possible_total_locked(self) -> int: ...
        def acquire_sample(
            self, thread_name: str | None = None
        ) -> ReverseFillAcquireResult: ...
        def commit_sample(
            self, thread_name: str | None = None
        ) -> int | None: ...
        def release_sample(
            self, thread_name: str | None = None, *, requeue: bool = False
        ) -> int | None: ...
        def mark_submission_failed(
            self, thread_name: str | None = None, *, max_retries: int = 1
        ) -> tuple[int | None, bool]: ...
        def is_target_unreachable(self) -> bool: ...

        def notify_runtime_change(self) -> None: ...
        def wait_for_runtime_change(
            self,
            *,
            stop_signal: threading.Event | None = None,
            timeout: float | None = None,
        ) -> bool: ...


class ReverseFillRuntimeMixin:
    def initialize_runtime(self: "_ReverseFillRuntimeHost") -> None:
        with self.lock:
            self.reverse_fill_runtime = create_reverse_fill_runtime_state(
                self.config.reverse_fill_spec
            )
        self.notify_runtime_change()

    def _thread_key(self, thread_name: str | None = None) -> str:
        key = str(thread_name or threading.current_thread().name or "Worker-?").strip()
        return key or "Worker-?"

    def _possible_total_locked(self: "_ReverseFillRuntimeHost") -> int:
        runtime = self.reverse_fill_runtime
        if runtime is None:
            return max(0, int(self.success_count or 0))
        return (
            max(0, int(self.success_count or 0))
            + len(runtime.queued_row_numbers)
            + len(runtime.reserved_row_by_thread)
        )

    def acquire_sample(
        self: "_ReverseFillRuntimeHost",
        thread_name: str | None = None,
    ) -> ReverseFillAcquireResult:
        key = self._thread_key(thread_name)
        with self.lock:
            runtime = self.reverse_fill_runtime
            if runtime is None:
                return ReverseFillAcquireResult(status="disabled", message="reverse_fill_disabled")
            existing_row = runtime.reserved_row_by_thread.get(key)
            if existing_row is not None:
                sample = runtime.samples_by_row_number.get(int(existing_row))
                if sample is not None:
                    return ReverseFillAcquireResult(
                        status="acquired", sample=sample, message="already_reserved"
                    )
                runtime.reserved_row_by_thread.pop(key, None)
            while runtime.queued_row_numbers:
                row_number = int(runtime.queued_row_numbers.popleft())
                sample = runtime.samples_by_row_number.get(row_number)
                if sample is None:
                    continue
                runtime.reserved_row_by_thread[key] = row_number
                return ReverseFillAcquireResult(
                    status="acquired", sample=sample, message="reserved"
                )
            target_num = max(0, int(self.config.target_num or 0))
            if target_num > 0 and self._possible_total_locked() < target_num:
                return ReverseFillAcquireResult(
                    status="exhausted", message="reverse_fill_target_unreachable"
                )
            return ReverseFillAcquireResult(status="waiting", message="reverse_fill_waiting")

    def release_sample(
        self: "_ReverseFillRuntimeHost",
        thread_name: str | None = None,
        *,
        requeue: bool = True,
    ) -> int | None:
        key = self._thread_key(thread_name)
        with self.lock:
            runtime = self.reverse_fill_runtime
            if runtime is None:
                return None
            row_number = runtime.reserved_row_by_thread.pop(key, None)
            if row_number is None:
                return None
            normalized_row = int(row_number)
            if (
                requeue
                and normalized_row not in runtime.committed_row_numbers
                and normalized_row not in runtime.discarded_row_numbers
            ):
                runtime.queued_row_numbers.appendleft(normalized_row)
        self.notify_runtime_change()
        return normalized_row

    def commit_sample(
        self: "_ReverseFillRuntimeHost",
        thread_name: str | None = None,
    ) -> int | None:
        key = self._thread_key(thread_name)
        with self.lock:
            runtime = self.reverse_fill_runtime
            if runtime is None:
                return None
            row_number = runtime.reserved_row_by_thread.pop(key, None)
            if row_number is None:
                return None
            normalized_row = int(row_number)
            runtime.committed_row_numbers.add(normalized_row)
            runtime.failure_count_by_row.pop(normalized_row, None)
        self.notify_runtime_change()
        return normalized_row

    def mark_submission_failed(
        self: "_ReverseFillRuntimeHost",
        thread_name: str | None = None,
        *,
        max_retries: int = 1,
    ) -> tuple[int | None, bool]:
        key = self._thread_key(thread_name)
        with self.lock:
            runtime = self.reverse_fill_runtime
            if runtime is None:
                return None, False
            row_number = runtime.reserved_row_by_thread.pop(key, None)
            if row_number is None:
                return None, False
            normalized_row = int(row_number)
            next_count = max(0, int(runtime.failure_count_by_row.get(normalized_row, 0))) + 1
            runtime.failure_count_by_row[normalized_row] = next_count
            if next_count <= max(0, int(max_retries or 0)):
                runtime.queued_row_numbers.appendleft(normalized_row)
                self.notify_runtime_change()
                return normalized_row, False
            runtime.discarded_row_numbers.add(normalized_row)
        self.notify_runtime_change()
        return normalized_row, True

    def wait_for_sample(
        self: "_ReverseFillRuntimeHost",
        *,
        thread_name: str | None = None,
        stop_signal: threading.Event | None = None,
        timeout_seconds: float = 0.5,
    ) -> ReverseFillAcquireResult:
        while True:
            result = self.acquire_sample(thread_name)
            if result.status != "waiting":
                return result
            if stop_signal is not None and stop_signal.is_set():
                return ReverseFillAcquireResult(status="waiting", message="stopped")
            if self.wait_for_runtime_change(stop_signal=stop_signal, timeout=timeout_seconds):
                return ReverseFillAcquireResult(status="waiting", message="stopped")

    def get_answer(
        self: "_ReverseFillRuntimeHost",
        question_num: int,
        thread_name: str | None = None,
    ) -> ReverseFillAnswer | None:
        key = self._thread_key(thread_name)
        normalized_question_num = int(question_num)
        with self.lock:
            runtime = self.reverse_fill_runtime
            if runtime is None:
                return None
            row_number = runtime.reserved_row_by_thread.get(key)
            if row_number is None:
                return None
            sample = runtime.samples_by_row_number.get(int(row_number))
            if sample is None:
                return None
            return (sample.answers or {}).get(normalized_question_num)

    def is_target_unreachable(self: "_ReverseFillRuntimeHost") -> bool:
        with self.lock:
            runtime = self.reverse_fill_runtime
            if runtime is None:
                return False
            target_num = max(0, int(self.config.target_num or 0))
            if target_num <= 0:
                return False
            return self._possible_total_locked() < target_num

    def complete_round(self: "_ReverseFillRuntimeHost", thread_name: str | None = None) -> int | None:
        return self.commit_sample(thread_name)

    def end_round(
        self: "_ReverseFillRuntimeHost",
        thread_name: str | None = None,
        *,
        submission_failed: bool = False,
        max_retries: int = 1,
    ) -> int | None:
        if submission_failed:
            row_number, _discarded = self.mark_submission_failed(
                thread_name, max_retries=max_retries
            )
            return row_number
        return self.release_sample(thread_name, requeue=True)
