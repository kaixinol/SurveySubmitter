from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:

    class _DistributionRuntimeHost(Protocol):
        lock: threading.Lock
        distribution_runtime_stats: dict[str, dict[str, Any]]
        distribution_pending_by_thread: dict[str, list[tuple[str, int, int]]]

        @staticmethod
        def _normalize_distribution_counts(raw_counts: Any, option_count: int) -> list[int]: ...
        def release_reverse_fill_sample(
            self, thread_name: str | None = None, *, requeue: bool = True
        ) -> int | None: ...

        def notify_runtime_change(self) -> None: ...
        def wait_for_runtime_change(
            self,
            *,
            stop_signal: threading.Event | None = None,
            timeout: float | None = None,
        ) -> bool: ...


class DistributionRuntimeMixin:
    @staticmethod
    def _normalize_distribution_counts(raw_counts: Any, option_count: int) -> list[int]:
        count = max(0, int(option_count or 0))
        normalized = [0] * count
        if not isinstance(raw_counts, list):
            return normalized
        for idx in range(min(len(raw_counts), count)):
            try:
                normalized[idx] = max(0, int(raw_counts[idx] or 0))
            except (ValueError, TypeError):
                normalized[idx] = 0
        return normalized

    def snapshot_distribution_stats(
        self: "_DistributionRuntimeHost",
        stat_key: str,
        option_count: int,
    ) -> tuple[int, list[int]]:
        with self.lock:
            bucket = self.distribution_runtime_stats.get(str(stat_key or "")) or {}
            total = max(0, int(bucket.get("total") or 0)) if isinstance(bucket, dict) else 0
            counts = self._normalize_distribution_counts(
                bucket.get("counts") if isinstance(bucket, dict) else None,
                option_count,
            )
        return total, counts

    def reset_pending_distribution(
        self: "_DistributionRuntimeHost", thread_name: str | None = None
    ) -> None:
        key = (
            str(thread_name or threading.current_thread().name or "Worker-?").strip() or "Worker-?"
        )
        with self.lock:
            self.distribution_pending_by_thread[key] = []

    def append_pending_distribution_choice(
        self: "_DistributionRuntimeHost",
        stat_key: str,
        option_index: int,
        option_count: int,
        thread_name: str | None = None,
    ) -> None:
        key = (
            str(thread_name or threading.current_thread().name or "Worker-?").strip() or "Worker-?"
        )
        normalized_option_count = max(0, int(option_count or 0))
        normalized_option_index = int(option_index or 0)
        if normalized_option_count <= 0:
            return
        if normalized_option_index < 0 or normalized_option_index >= normalized_option_count:
            return
        item = (str(stat_key or ""), normalized_option_index, normalized_option_count)
        with self.lock:
            pending = self.distribution_pending_by_thread.setdefault(key, [])
            pending.append(item)

    def commit_pending_distribution(
        self: "_DistributionRuntimeHost", thread_name: str | None = None
    ) -> int:
        key = (
            str(thread_name or threading.current_thread().name or "Worker-?").strip() or "Worker-?"
        )
        committed = 0
        with self.lock:
            pending = list(self.distribution_pending_by_thread.get(key) or [])
            self.distribution_pending_by_thread[key] = []
            for stat_key, option_index, option_count in pending:
                if option_count <= 0 or option_index < 0 or option_index >= option_count:
                    continue
                bucket = self.distribution_runtime_stats.get(stat_key) or {}
                total = max(0, int(bucket.get("total") or 0)) if isinstance(bucket, dict) else 0
                counts = self._normalize_distribution_counts(
                    bucket.get("counts") if isinstance(bucket, dict) else None,
                    option_count,
                )
                counts[option_index] += 1
                self.distribution_runtime_stats[stat_key] = {
                    "total": total + 1,
                    "counts": counts,
                }
                committed += 1
        return committed
