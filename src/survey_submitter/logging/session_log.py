from __future__ import annotations

import logging
import os
import shutil
import threading
from datetime import datetime

from survey_submitter.constants import LOG_FORMAT
from survey_submitter.constants import (
    AUTO_SAVE_LOG_RETENTION_COUNT_SETTING_KEY,
    AUTO_SAVE_LOG_RETENTION_OPTIONS,
    AUTO_SAVE_LOGS_SETTING_KEY,
    DEFAULT_AUTO_SAVE_LOG_RETENTION_COUNT,
    DEFAULT_AUTO_SAVE_LOGS,
)
from survey_submitter.io.config.settings_store import (
    app_settings,
    get_bool_setting,
    get_int_setting,
)
from survey_submitter.system.paths import get_user_logs_directory

import survey_submitter.logging.log_utils as _log_utils


_SESSION_LOG_HANDLER: logging.Handler | None = None
_SESSION_LOG_PATH = ""
_SESSION_LOG_LOCK = threading.Lock()
_SESSION_LOG_BACKFILLED = False
_DELETE_SESSION_LOG_ON_SHUTDOWN = False


def _create_session_log_file_path() -> str:
    logs_dir = get_user_logs_directory()
    os.makedirs(logs_dir, exist_ok=True)
    file_name = datetime.now().strftime("session_%Y%m%d_%H%M%S.log")
    return os.path.join(logs_dir, file_name)


def _backfill_session_log_from_buffer() -> None:
    global _SESSION_LOG_BACKFILLED
    if _SESSION_LOG_BACKFILLED or not _SESSION_LOG_PATH:
        return


    records = _log_utils.LOG_BUFFER_HANDLER.get_records()
    if not records:
        _SESSION_LOG_BACKFILLED = True
        return
    try:
        with open(_SESSION_LOG_PATH, "a", encoding="utf-8") as file:
            for entry in records:
                text = str(getattr(entry, "text", "") or "")
                if text:
                    file.write(text)
                    file.write("\n")
        _SESSION_LOG_BACKFILLED = True
    except OSError as exc:
        _log_utils._safe_internal_log("backfill session log from buffer failed", exc)


def _ensure_session_log_handler(root_logger: logging.Logger | None = None) -> str:
    global _SESSION_LOG_HANDLER, _SESSION_LOG_PATH

    logger = root_logger or logging.getLogger()
    with _SESSION_LOG_LOCK:
        if _SESSION_LOG_HANDLER is not None and _SESSION_LOG_PATH:
            return _SESSION_LOG_PATH

        session_log_path = _create_session_log_file_path()
        handler = _log_utils.AsyncFileHandler(session_log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
        _SESSION_LOG_HANDLER = handler
        _SESSION_LOG_PATH = session_log_path
        _backfill_session_log_from_buffer()
        return session_log_path


def flush_session_log_file() -> None:
    handler = _SESSION_LOG_HANDLER
    if handler is None:
        return
    with _SESSION_LOG_LOCK:
        try:
            handler.flush()
        except OSError as exc:
            _log_utils._safe_internal_log("flush_session_log_file failed", exc)


def get_current_session_log_path() -> str:
    return str(_SESSION_LOG_PATH or "")


def _ensure_logs_dir(runtime_directory: str) -> str:
    normalized = os.path.abspath(str(runtime_directory or "").strip())
    if not normalized:
        raise ValueError("runtime_directory \u4e0d\u80fd\u4e3a\u7a7a")

    candidate_name = os.path.basename(normalized).lower()
    if candidate_name == "logs":
        logs_dir = normalized
    else:
        logs_dir = os.path.join(normalized, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


def get_auto_save_log_settings() -> tuple[bool, int]:

    settings = app_settings()
    enabled = get_bool_setting(settings.value(AUTO_SAVE_LOGS_SETTING_KEY), DEFAULT_AUTO_SAVE_LOGS)
    max_keep = (
        max(AUTO_SAVE_LOG_RETENTION_OPTIONS)
        if AUTO_SAVE_LOG_RETENTION_OPTIONS
        else DEFAULT_AUTO_SAVE_LOG_RETENTION_COUNT
    )
    keep_count = get_int_setting(
        settings.value(AUTO_SAVE_LOG_RETENTION_COUNT_SETTING_KEY),
        DEFAULT_AUTO_SAVE_LOG_RETENTION_COUNT,
    )
    keep_count = max(1, min(keep_count, max_keep))
    return bool(enabled), int(keep_count)


def prune_session_log_files(runtime_directory: str, keep_count: int) -> int:

    logs_dir = _ensure_logs_dir(runtime_directory)
    keep_count = max(1, int(keep_count))
    candidates: list[tuple[float, str]] = []
    for name in os.listdir(logs_dir):
        if not (name.startswith("session_") and name.endswith(".log")):
            continue
        path = os.path.join(logs_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            candidates.append((os.path.getmtime(path), path))
        except OSError:
            continue
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

    removed = 0
    for _mtime, path in candidates[keep_count:]:
        try:
            os.remove(path)
            removed += 1
        except OSError as exc:
            _log_utils._safe_internal_log(f"prune_session_log_files failed: {path}", exc)
    return removed


def finalize_session_log_persistence(runtime_directory: str) -> None:

    global _DELETE_SESSION_LOG_ON_SHUTDOWN

    enabled, keep_count = get_auto_save_log_settings()
    logs_dir = _ensure_logs_dir(runtime_directory)
    last_session_path = os.path.join(logs_dir, "last_session.log")

    if enabled:
        export_full_log_to_file(
            runtime_directory,
            last_session_path,
            fallback_records=_log_utils.LOG_BUFFER_HANDLER.get_records(),
        )
        prune_session_log_files(runtime_directory, keep_count)
        _DELETE_SESSION_LOG_ON_SHUTDOWN = False
        return

    _DELETE_SESSION_LOG_ON_SHUTDOWN = True
    try:
        if os.path.isfile(last_session_path):
            os.remove(last_session_path)
    except OSError as exc:
        _log_utils._safe_internal_log(
            "finalize_session_log_persistence failed to remove last_session.log", exc
        )


def save_log_records_to_file(
    records,
    runtime_directory: str,
    file_path: str | None = None,
) -> str:

    if not runtime_directory:
        raise ValueError("runtime_directory \u4e0d\u80fd\u4e3a\u7a7a")
    if file_path:
        parent_dir = os.path.dirname(file_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
    else:
        logs_dir = _ensure_logs_dir(runtime_directory)
        file_name = datetime.now().strftime("log_%Y%m%d_%H%M%S.txt")
        file_path = os.path.join(logs_dir, file_name)
    text_records = [entry.text for entry in (records or [])]
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(text_records))
    return file_path


def export_full_log_to_file(
    runtime_directory: str,
    file_path: str | None = None,
    *,
    fallback_records=None,
) -> str:
    if not runtime_directory:
        raise ValueError("runtime_directory \u4e0d\u80fd\u4e3a\u7a7a")
    if file_path:
        parent_dir = os.path.dirname(file_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
    else:
        logs_dir = _ensure_logs_dir(runtime_directory)
        file_name = datetime.now().strftime("log_%Y%m%d_%H%M%S.txt")
        file_path = os.path.join(logs_dir, file_name)

    session_log_path = get_current_session_log_path()
    if session_log_path and os.path.isfile(session_log_path):
        flush_session_log_file()
        src = os.path.abspath(session_log_path)
        dst = os.path.abspath(file_path)
        if os.path.normcase(src) == os.path.normcase(dst):
            return file_path
        try:
            with (
                open(src, "r", encoding="utf-8") as source,
                open(dst, "w", encoding="utf-8") as target,
            ):
                shutil.copyfileobj(source, target)
            return file_path
        except OSError as exc:
            _log_utils._safe_internal_log(
                "export_full_log_to_file fallback to buffer failed to read session log", exc
            )

    records = (
        fallback_records
        if fallback_records is not None
        else _log_utils.LOG_BUFFER_HANDLER.get_records()
    )
    return save_log_records_to_file(records, runtime_directory, file_path)
