from __future__ import annotations

import atexit
import logging
import os
import queue
import sys
import threading
import traceback
from datetime import datetime
from typing import Any, Callable

from survey_submitter.constants import LOG_BUFFER_CAPACITY, LOG_FORMAT

ORIGINAL_STDOUT = sys.stdout
ORIGINAL_STDERR = sys.stderr
ORIGINAL_EXCEPTHOOK = sys.excepthook
_popup_handler: Callable[[str, str, str], Any] | None = None
_NOISY_LOG_PATTERNS = (
    "QFluentWidgets Pro is now released",
    "https://qfluentwidgets.com/pages/pro",
)
_SUPPRESSED_RUNTIME_NOISE_PATTERNS = (
    "WJX \u9875\u9762\u9898\u76ee\u5feb\u7167\u5237\u65b0",
    "WJX \u9898\u76ee\u5904\u7406\u8017\u65f6",
    "\u968f\u673a\u4ee3\u7406\u9996\u8f7d\uff1a\u63a2\u6d4b\u9875\u9762\u53ef\u7528\u6027",
    "\u968f\u673a\u4ee3\u7406\u9996\u8f7d\u63a2\u6d4b\u6210\u529f",
    "\u968f\u673a\u4ee3\u7406\u9996\u8f7d\uff1a\u9875\u9762\u4ecd\u5728\u52a0\u8f7d\uff0c\u5148\u4fdd\u6301\u539f\u9875\u8ffd\u52a0\u63a2\u6d4b",
    "\u968f\u673a\u4ee3\u7406\u9996\u8f7d\u5bbd\u9650\u63a2\u6d4b\u6210\u529f",
    "\u968f\u673a\u4ee3\u7406\u9996\u8f7d\uff1a\u7ee7\u7eed\u7b49\u5f85\u539f\u9875\u9762\u5b8c\u6210\u52a0\u8f7d",
    "\u968f\u673a\u4ee3\u7406\u6162\u52a0\u8f7d\u63a2\u6d4b\u6210\u529f",
)
_DEDUPED_LOG_STATE: dict[str, str] = {}
_DEDUPED_LOG_LOCK = threading.Lock()


def _should_filter_noise(message: str) -> bool:
    
    if message is None:
        return True
    text = str(message)
    if not text.strip():
        return True
    return any(pattern in text for pattern in _NOISY_LOG_PATTERNS) or any(
        pattern in text for pattern in _SUPPRESSED_RUNTIME_NOISE_PATTERNS
    )


def _safe_internal_log(message: str, exc: BaseException | None = None) -> None:
    
    try:
        ORIGINAL_STDERR.write(f"[LogInternal] {message}\n")
        if exc is not None:
            ORIGINAL_STDERR.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        ORIGINAL_STDERR.flush()
    except OSError:
        try:
            ORIGINAL_STDERR.write("[LogInternal] safe log failed\n")
            ORIGINAL_STDERR.flush()
        except OSError:
            return


def log_suppressed_exception(
    context: str,
    exc: BaseException | None = None,
    *,
    level: int = logging.INFO,
) -> None:
    
    if exc is None:
        logging.log(level, "[Suppressed] %s", context)
    else:
        logging.log(level, "[Suppressed] %s: %s", context, exc, exc_info=True)


def log_deduped_message(
    key: str,
    message: str,
    *,
    level: int = logging.INFO,
) -> bool:
    
    normalized_key = str(key or "").strip()
    normalized_message = str(message or "").strip()
    if not normalized_key or not normalized_message:
        return False
    with _DEDUPED_LOG_LOCK:
        if _DEDUPED_LOG_STATE.get(normalized_key) == normalized_message:
            return False
        _DEDUPED_LOG_STATE[normalized_key] = normalized_message
    logging.log(level, normalized_message)
    return True


def reset_deduped_log_message(key: str) -> None:
    
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return
    with _DEDUPED_LOG_LOCK:
        _DEDUPED_LOG_STATE.pop(normalized_key, None)


class AsyncFileHandler(logging.Handler):
    

    _STOP = object()

    def __init__(self, filename: str, *, encoding: str = "utf-8", batch_size: int = 200):
        super().__init__()
        self.baseFilename = os.path.abspath(filename)
        self.encoding = encoding
        self._batch_size = max(1, int(batch_size or 1))
        self._queue: queue.Queue = queue.Queue(maxsize=10000)
        self._closed = False
        self._write_lock = threading.Lock()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="SessionLogFileWriter",
        )
        self._worker_thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            _safe_internal_log("AsyncFileHandler queue full, dropping log")
        except OSError:
            self.handleError(record)

    def _worker_loop(self) -> None:
        try:
            with open(self.baseFilename, "a", encoding=self.encoding) as stream:
                while True:
                    item = self._queue.get()
                    if item is self._STOP:
                        break
                    batch = [item]
                    while len(batch) < self._batch_size:
                        try:
                            next_item = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        if next_item is self._STOP:
                            self._queue.put_nowait(self._STOP)
                            break
                        batch.append(next_item)
                    with self._write_lock:
                        for record in batch:
                            try:
                                stream.write(self.format(record))
                                stream.write("\n")
                            except OSError as exc:
                                _safe_internal_log("AsyncFileHandler write failed", exc)
                        stream.flush()
        except OSError as exc:
            _safe_internal_log("AsyncFileHandler worker failed", exc)

    def flush(self) -> None:
        deadline = datetime.now().timestamp() + 2.0
        while not self._queue.empty() and datetime.now().timestamp() < deadline:
            threading.Event().wait(0.01)
        with self._write_lock:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(self._STOP)
        except (queue.Full, OSError):
            pass
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        super().close()


from survey_submitter.logging.log_buffer_handler import LogBufferHandler, LogBufferEntry  # noqa: E402
from survey_submitter.logging.stream_redirect import StreamToLogger  # noqa: E402
from survey_submitter.logging.session_log import (  # noqa: E402
    _backfill_session_log_from_buffer,
    _ensure_session_log_handler,
    _ensure_logs_dir,
    export_full_log_to_file,
    finalize_session_log_persistence,
    flush_session_log_file,
    get_auto_save_log_settings,
    get_current_session_log_path,
    prune_session_log_files,
    save_log_records_to_file,
)
import survey_submitter.logging.session_log as _session_log  # noqa: E402


LOG_BUFFER_HANDLER = LogBufferHandler()

_root_logger = logging.getLogger()
if not any(isinstance(h, LogBufferHandler) for h in _root_logger.handlers):
    _root_logger.addHandler(LOG_BUFFER_HANDLER)
_root_logger.setLevel(logging.INFO)
atexit.register(lambda: shutdown_logging())


def setup_logging():
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    root_logger.setLevel(logging.INFO)
    if not any(isinstance(handler, LogBufferHandler) for handler in root_logger.handlers):
        root_logger.addHandler(LOG_BUFFER_HANDLER)
    _ensure_session_log_handler(root_logger)

    
    for noisy_logger in ("urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    if not getattr(setup_logging, "_streams_hooked", False):
        stdout_logger = StreamToLogger(root_logger, logging.INFO, stream=ORIGINAL_STDOUT)
        stderr_logger = StreamToLogger(root_logger, logging.ERROR, stream=ORIGINAL_STDERR)
        sys.stdout = stdout_logger
        sys.stderr = stderr_logger

        def _handle_unhandled_exception(exc_type, exc_value, exc_traceback):
            if issubclass(exc_type, KeyboardInterrupt):
                if ORIGINAL_EXCEPTHOOK:
                    ORIGINAL_EXCEPTHOOK(exc_type, exc_value, exc_traceback)
                return
            root_logger.error("\u672a\u5904\u7406\u7684\u5f02\u5e38", exc_info=(exc_type, exc_value, exc_traceback))
            if ORIGINAL_EXCEPTHOOK:
                ORIGINAL_EXCEPTHOOK(exc_type, exc_value, exc_traceback)

        sys.excepthook = _handle_unhandled_exception
        setattr(setup_logging, "_streams_hooked", True)


def register_popup_handler(handler: Callable[[str, str, str], Any] | None) -> None:
    
    global _popup_handler
    _popup_handler = handler


def _dispatch_popup(kind: str, title: str, message: str, default: Any = None) -> Any:
    
    logging.log(
        logging.INFO if kind in {"info", "confirm"} else logging.ERROR if kind == "error" else logging.WARNING,
        f"[Popup {kind.upper()}] {title} | {message}",
    )
    if _popup_handler:
        try:
            return _popup_handler(kind, title, message)
        except Exception:  
            logging.info("popup handler failed", exc_info=True)
    return default


def log_popup_error(title: str, message: str, **kwargs: Any):
    
    _ = kwargs
    return _dispatch_popup("error", title, message, default=False)


def log_popup_warning(title: str, message: str, **kwargs: Any):
    
    _ = kwargs
    return _dispatch_popup("warning", title, message, default=True)


def log_popup_confirm(title: str, message: str, **kwargs: Any) -> bool:
    
    _ = kwargs
    return bool(_dispatch_popup("confirm", title, message, default=False))


def shutdown_logging():
    
    try:
        session_log_path = str(_session_log._SESSION_LOG_PATH or "")
        
        LOG_BUFFER_HANDLER.flush_remaining()
        flush_session_log_file()

        
        LOG_BUFFER_HANDLER.stop()

        
        sys.stdout = ORIGINAL_STDOUT
        sys.stderr = ORIGINAL_STDERR

        
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)
        if _session_log._DELETE_SESSION_LOG_ON_SHUTDOWN and session_log_path and os.path.isfile(session_log_path):
            try:
                os.remove(session_log_path)
            except OSError as exc:
                _safe_internal_log("shutdown_logging failed to remove session log", exc)
    except OSError as exc:
        _safe_internal_log("shutdown_logging failed", exc)
