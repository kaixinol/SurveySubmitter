from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable

from survey_submitter.constants import LOG_BUFFER_CAPACITY, LOG_FORMAT
from survey_submitter.logging.log_listener_manager import ListenerManager
from survey_submitter.logging.log_message_processing import (
    apply_category_label,
    determine_category,
    should_filter_sensitive,
    strip_ansi_codes,
)


@dataclass
class LogBufferEntry:
    text: str
    category: str


class LogBufferHandler(logging.Handler):
    def __init__(self, capacity: int = LOG_BUFFER_CAPACITY):
        super().__init__()
        self.capacity = capacity

        self._queue: queue.Queue = queue.Queue(maxsize=max(1000, int(capacity or 0) * 4))

        self._records: deque[LogBufferEntry] = deque(maxlen=capacity if capacity else None)
        self._records_lock = threading.RLock()

        self._listener_manager = ListenerManager()

        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))

        self._start_worker()

    def _start_worker(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="LogBufferWorker"
        )
        self._worker_thread.start()

    def _worker_loop(self):
        while not self._stop_event.is_set():
            try:
                batch = []
                try:
                    record = self._queue.get(timeout=0.1)
                    batch.append(record)

                    while len(batch) < 100:
                        try:
                            record = self._queue.get_nowait()
                            batch.append(record)
                        except queue.Empty:
                            break
                except queue.Empty:
                    continue

                for record in batch:
                    try:
                        self._process_record(record)
                    except Exception as exc:
                        from survey_submitter.logging.log_utils import _safe_internal_log

                        _safe_internal_log("LogBufferHandler _process_record failed", exc)

                current_version = self._listener_manager.increment_version()
                self._listener_manager.notify_listeners(current_version)

            except OSError as exc:
                from survey_submitter.logging.log_utils import _safe_internal_log

                _safe_internal_log("LogBufferHandler worker loop failed", exc)

    def _process_record(self, record: logging.LogRecord):
        original_level = record.levelname
        message = self.format(record)

        if should_filter_sensitive(message):
            return

        from survey_submitter.logging.log_utils import _should_filter_noise

        if _should_filter_noise(message):
            return

        message = strip_ansi_codes(message)

        category = determine_category(record, message)

        display_text = apply_category_label(message, original_level, category)

        entry = LogBufferEntry(text=display_text, category=category)
        with self._records_lock:
            self._records.append(entry)

    def emit(self, record: logging.LogRecord):
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            from survey_submitter.logging.log_utils import _safe_internal_log

            _safe_internal_log("LogBufferHandler queue full, dropping log")
        except OSError:
            self.handleError(record)

    def get_records(self, _try_lock: bool = False) -> list[LogBufferEntry]:
        with self._records_lock:
            return list(self._records)

    def get_version(self) -> int:
        return self._listener_manager.get_version()

    def add_listener(self, listener: Callable[[int], None]) -> int:
        return self._listener_manager.add_listener(listener)

    def remove_listener(self, listener_id: int) -> None:
        self._listener_manager.remove_listener(listener_id)

    def stop(self):
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)

    def flush_remaining(self):
        try:
            processed = False
            while not self._queue.empty():
                try:
                    record = self._queue.get_nowait()
                    self._process_record(record)
                    processed = True
                except queue.Empty:
                    break
            if processed:
                current_version = self._listener_manager.increment_version()
                self._listener_manager.notify_listeners(current_version)
        except OSError as exc:
            from survey_submitter.logging.log_utils import _safe_internal_log

            _safe_internal_log("LogBufferHandler flush_remaining failed", exc)
