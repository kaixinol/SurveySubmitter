from __future__ import annotations

import atexit
import logging
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from loguru import logger

from survey_submitter.constants import LOG_FORMAT

_ORIGINAL_STDOUT = sys.stdout
_ORIGINAL_STDERR = sys.stderr
_ORIGINAL_EXCEPTHOOK = sys.excepthook

_SUPPRESSED_RUNTIME_NOISE_PATTERNS = (
    "WJX 页面题目快照刷新",
    "WJX 题目处理耗时",
    "随机代理首载：探测页面可用性",
    "随机代理首载探测成功",
    "随机代理首载：页面仍在加载，先保持原页追加探测",
    "随机代理首载宽限探测成功",
    "随机代理首载：继续等待原页面完成加载",
    "随机代理慢加载探测成功",
)
_DEDUPED_LOG_STATE: dict[str, str] = {}
_DEDUPED_LOG_LOCK = threading.Lock()


def _should_filter_noise(message: str) -> bool:
    if message is None:
        return True
    text = str(message)
    if not text.strip():
        return True
    return any(pattern in text for pattern in _SUPPRESSED_RUNTIME_NOISE_PATTERNS)


def _safe_internal_log(message: str, exc: BaseException | None = None) -> None:
    try:
        _ORIGINAL_STDERR.write(f"[LogInternal] {message}\n")
        if exc is not None:
            _ORIGINAL_STDERR.write(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            )
        _ORIGINAL_STDERR.flush()
    except OSError:
        try:
            _ORIGINAL_STDERR.write("[LogInternal] safe log failed\n")
            _ORIGINAL_STDERR.flush()
        except OSError:
            return


def log_suppressed_exception(
    context: str,
    exc: BaseException | None = None,
    *,
    level: str = "INFO",
) -> None:
    if exc is None:
        logger.log(level, f"[Suppressed] {context}")
    else:
        logger.log(level, f"[Suppressed] {context}: {exc}")


def log_deduped_message(
    key: str,
    message: str,
    *,
    level: str = "INFO",
) -> bool:
    normalized_key = str(key or "").strip()
    normalized_message = str(message or "").strip()
    if not normalized_key or not normalized_message:
        return False
    with _DEDUPED_LOG_LOCK:
        if _DEDUPED_LOG_STATE.get(normalized_key) == normalized_message:
            return False
        _DEDUPED_LOG_STATE[normalized_key] = normalized_message
    logger.log(level, normalized_message)
    return True


def reset_deduped_log_message(key: str) -> None:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return
    with _DEDUPED_LOG_LOCK:
        _DEDUPED_LOG_STATE.pop(normalized_key, None)


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format=LOG_FORMAT,
        level="INFO",
        filter=lambda record: not _should_filter_noise(str(record["message"])),
        colorize=False,
    )

    from survey_submitter.logging.session_log import _ensure_session_log_sink

    _ensure_session_log_sink()

    logging.root.handlers = [_InterceptHandler()]
    logging.root.setLevel("INFO")

    for noisy_logger in ("urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel("WARNING")

    if not getattr(setup_logging, "_excepthook_installed", False):

        def _handle_unhandled_exception(
            exc_type: type[BaseException],
            exc_value: BaseException,
            exc_traceback: Any,
        ) -> None:
            if issubclass(exc_type, KeyboardInterrupt):
                if _ORIGINAL_EXCEPTHOOK:
                    _ORIGINAL_EXCEPTHOOK(exc_type, exc_value, exc_traceback)
                return
            logger.error("未处理的异常", exc=(exc_type, exc_value, exc_traceback))
            if _ORIGINAL_EXCEPTHOOK:
                _ORIGINAL_EXCEPTHOOK(exc_type, exc_value, exc_traceback)

        sys.excepthook = _handle_unhandled_exception
        setattr(setup_logging, "_excepthook_installed", True)


def shutdown_logging() -> None:
    try:
        from survey_submitter.logging import session_log as _session_log

        session_log_path = str(_session_log.get_current_session_log_path() or "")

        _session_log._remove_session_log_sink()

        logger.remove()

        if (
            _session_log._DELETE_SESSION_LOG_ON_SHUTDOWN
            and session_log_path
            and Path(session_log_path).is_file()
        ):
            try:
                Path(session_log_path).unlink()
            except OSError as exc:
                _safe_internal_log("shutdown_logging failed to remove session log", exc)
    except OSError as exc:
        _safe_internal_log("shutdown_logging failed", exc)


atexit.register(lambda: shutdown_logging())


__all__ = [
    "log_deduped_message",
    "log_suppressed_exception",
    "reset_deduped_log_message",
    "setup_logging",
    "shutdown_logging",
]
