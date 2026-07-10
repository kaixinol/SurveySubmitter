from __future__ import annotations

import logging
import queue
import re
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable

from survey_submitter.constants import LOG_BUFFER_CAPACITY, LOG_FORMAT
from survey_submitter.logging.log_utils import _safe_internal_log, _should_filter_noise


@dataclass
class LogBufferEntry:
    text: str
    category: str


class LogBufferHandler(logging.Handler):

    

    _ANSI_ESCAPE_PATTERN = re.compile(r'\x1b\[[0-9;]*m')

    def __init__(self, capacity: int = LOG_BUFFER_CAPACITY):
        super().__init__()
        self.capacity = capacity

        
        self._queue: queue.Queue = queue.Queue(maxsize=max(1000, int(capacity or 0) * 4))

        
        self._records: deque[LogBufferEntry] = deque(maxlen=capacity if capacity else None)
        self._records_lock = threading.RLock()

        
        self._version = 0
        self._version_lock = threading.Lock()
        self._listeners: dict[int, Callable[[int], None]] = {}
        self._listeners_lock = threading.Lock()

        
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))

        
        self._start_worker()

    def _start_worker(self):
        
        if self._worker_thread and self._worker_thread.is_alive():
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="LogBufferWorker"
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
                    self._process_record(record)

                
                with self._version_lock:
                    self._version += 1
                    current_version = self._version
                self._notify_listeners(current_version)

            except Exception as exc:
                
                _safe_internal_log("LogBufferHandler worker loop failed", exc)

    def _process_record(self, record: logging.LogRecord):
        
        try:
            original_level = record.levelname
            message = self.format(record)

            
            if self._should_filter_sensitive(message):
                return
            
            if _should_filter_noise(message):
                return

            
            message = self._strip_ansi_codes(message)

            
            category = self._determine_category(record, message)

            
            display_text = self._apply_category_label(message, original_level, category)

            
            entry = LogBufferEntry(text=display_text, category=category)
            with self._records_lock:
                self._records.append(entry)

        except Exception as exc:
            
            _safe_internal_log("LogBufferHandler process_record failed", exc)

    def emit(self, record: logging.LogRecord):
        
        try:
            
            self._queue.put_nowait(record)
        except queue.Full:
            
            _safe_internal_log("LogBufferHandler queue full, dropping log")
        except Exception:
            self.handleError(record)

    def get_records(self, _try_lock: bool = False) -> list[LogBufferEntry]:
        
        with self._records_lock:
            return list(self._records)

    def get_version(self) -> int:
        
        with self._version_lock:
            return self._version

    def add_listener(self, listener: Callable[[int], None]) -> int:
        
        global _LOG_LISTENER_ID
        if not callable(listener):
            return 0
        with self._listeners_lock:
            _LOG_LISTENER_ID += 1
            listener_id = _LOG_LISTENER_ID
            self._listeners[listener_id] = listener
            return listener_id

    def remove_listener(self, listener_id: int) -> None:
        if not listener_id:
            return
        with self._listeners_lock:
            self._listeners.pop(int(listener_id), None)

    def _notify_listeners(self, version: int) -> None:
        with self._listeners_lock:
            listeners = list(self._listeners.values())
        for listener in listeners:
            try:
                listener(version)
            except Exception as exc:
                _safe_internal_log("LogBufferHandler listener failed", exc)

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
                with self._version_lock:
                    self._version += 1
                    current_version = self._version
                self._notify_listeners(current_version)
        except Exception as exc:
            _safe_internal_log("LogBufferHandler flush_remaining failed", exc)

    @staticmethod
    def _strip_ansi_codes(text: str) -> str:
        
        if not text:
            return text
        return LogBufferHandler._ANSI_ESCAPE_PATTERN.sub('', text)

    @staticmethod
    def _should_filter_sensitive(message: str) -> bool:
        
        if not message:
            return False
        sensitive_patterns = [
            "Authorization: Bearer ",
            "refresh_token",
            "access_token",
        ]
        return any(pattern in message for pattern in sensitive_patterns)

    @staticmethod
    def _determine_category(record: logging.LogRecord, message: str) -> str:
        custom_category = getattr(record, "log_category", None)
        if isinstance(custom_category, str):
            normalized = custom_category.strip().upper()
            if normalized in {"INFO", "OK", "WARNING", "ERROR"}:
                return normalized

        level = record.levelname.upper()
        if level in {"ERROR", "CRITICAL"}:
            return "ERROR"
        if level == "WARNING":
            return "WARNING"
        if level in {"OK", "SUCCESS"}:
            return "OK"

        normalized_message = message.upper()
        ok_markers = ("[OK]", "[SUCCESS]")
        ok_keywords = (
            "\u6210\u529f",
            "\u5df2\u5b8c\u6210",
            "\u89e3\u6790\u5b8c\u6210",
            "\u586b\u5199\u5b8c\u6210",
            "\u586b\u5199\u6210\u529f",
            "\u63d0\u4ea4\u6210\u529f",
            "\u4fdd\u5b58\u6210\u529f",
            "\u6062\u590d\u6210\u529f",
            "\u52a0\u8f7d\u4e0a\u6b21\u914d\u7f6e",
            "\u5df2\u52a0\u8f7d\u4e0a\u6b21\u914d\u7f6e",
            "\u52a0\u8f7d\u5b8c\u6210",
        )
        negative_keywords = ("\u672a\u6210\u529f", "\u672a\u5b8c\u6210", "\u5931\u8d25", "\u9519\u8bef", "\u5f02\u5e38")
        if any(marker in message for marker in ok_markers):
            return "OK"
        if normalized_message.startswith("OK"):
            return "OK"
        if any(keyword in message for keyword in ok_keywords):
            if not any(neg in message for neg in negative_keywords):
                return "OK"

        return "INFO"

    @staticmethod
    def _apply_category_label(message: str, original_level: str, category: str) -> str:
        if not message or not original_level:
            return message
        original_label = f"[{original_level.upper()}]"
        replacement_label = f"[{category.upper()}]"

        deduped = LogBufferHandler._collapse_adjacent_label(message, original_label, replacement_label)
        if deduped is not None:
            return deduped

        if category.upper() == original_level.upper():
            return message
        if original_label in message:
            return message.replace(original_label, replacement_label, 1)
        return message

    @staticmethod
    def _collapse_adjacent_label(message: str, original_label: str, target_label: str) -> str | None:
        if not message or not original_label or not target_label:
            return None
        index = message.find(original_label)
        if index == -1:
            return None
        remainder = message[index + len(original_label):]
        trimmed = remainder.lstrip()
        if not trimmed.startswith(target_label):
            return None
        whitespace = remainder[: len(remainder) - len(trimmed)]
        suffix = trimmed[len(target_label):]
        return f"{message[:index]}{target_label}{whitespace}{suffix}"


_LOG_LISTENER_ID = 0
