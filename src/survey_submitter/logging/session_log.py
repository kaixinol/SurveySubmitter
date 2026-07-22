from __future__ import annotations

import shutil
import threading
from datetime import datetime
from pathlib import Path

from loguru import logger

from survey_submitter.constants import LOG_FORMAT
from survey_submitter.constants import (
    AUTO_SAVE_LOG_RETENTION_COUNT_KEY,
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
from survey_submitter.logging.log_utils import _safe_internal_log
from survey_submitter.system.paths import get_user_logs_directory

_SESSION_LOG_SINK_ID: int | None = None
_SESSION_LOG_PATH = ""
_SESSION_LOG_LOCK = threading.Lock()
_DELETE_SESSION_LOG_ON_SHUTDOWN = False


def _create_session_log_file_path() -> str:
    logs_dir = get_user_logs_directory()
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    file_name = datetime.now().strftime("session_%Y%m%d_%H%M%S.log")
    return str(Path(logs_dir) / file_name)


def _ensure_session_log_sink() -> str:
    global _SESSION_LOG_SINK_ID, _SESSION_LOG_PATH

    with _SESSION_LOG_LOCK:
        if _SESSION_LOG_SINK_ID is not None and _SESSION_LOG_PATH:
            return _SESSION_LOG_PATH

        session_log_path = _create_session_log_file_path()
        sink_id = logger.add(
            session_log_path,
            format=LOG_FORMAT,
            level="DEBUG",
            encoding="utf-8",
            rotation="10 MB",
        )
        _SESSION_LOG_SINK_ID = sink_id
        _SESSION_LOG_PATH = session_log_path
        return session_log_path


def _remove_session_log_sink() -> None:
    global _SESSION_LOG_SINK_ID

    sink_id = _SESSION_LOG_SINK_ID
    if sink_id is None:
        return
    with _SESSION_LOG_LOCK:
        try:
            logger.remove(sink_id)
        except (ValueError, KeyError):
            pass
        _SESSION_LOG_SINK_ID = None


def get_current_session_log_path() -> str:
    return str(_SESSION_LOG_PATH or "")


def _ensure_logs_dir(runtime_directory: str) -> str:
    normalized = str(Path(str(runtime_directory or "").strip()).resolve())
    if not normalized:
        raise ValueError("runtime_directory 不能为空")

    candidate_name = Path(normalized).name.lower()
    if candidate_name == "logs":
        logs_dir = normalized
    else:
        logs_dir = str(Path(normalized) / "logs")
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
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
        settings.value(AUTO_SAVE_LOG_RETENTION_COUNT_KEY),
        DEFAULT_AUTO_SAVE_LOG_RETENTION_COUNT,
    )
    keep_count = max(1, min(keep_count, max_keep))
    return bool(enabled), int(keep_count)


def prune_session_log_files(runtime_directory: str, keep_count: int) -> int:
    logs_dir = _ensure_logs_dir(runtime_directory)
    keep_count = max(1, int(keep_count))
    candidates: list[tuple[float, str]] = []
    for path in Path(logs_dir).iterdir():
        name = path.name
        if not (name.startswith("session_") and name.endswith(".log")):
            continue
        if not path.is_file():
            continue
        try:
            candidates.append((path.stat().st_mtime, str(path)))
        except OSError:
            continue
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

    removed = 0
    for _mtime, path_str in candidates[keep_count:]:
        try:
            Path(path_str).unlink()
            removed += 1
        except OSError as exc:
            _safe_internal_log(f"prune_session_log_files failed: {path_str}", exc)
    return removed


def finalize_session_log_persistence(runtime_directory: str) -> None:
    global _DELETE_SESSION_LOG_ON_SHUTDOWN

    enabled, keep_count = get_auto_save_log_settings()
    logs_dir = _ensure_logs_dir(runtime_directory)
    last_session_path = str(Path(logs_dir) / "last_session.log")

    if enabled:
        export_full_log_to_file(runtime_directory, last_session_path)
        prune_session_log_files(runtime_directory, keep_count)
        _DELETE_SESSION_LOG_ON_SHUTDOWN = False
        return

    _DELETE_SESSION_LOG_ON_SHUTDOWN = True
    try:
        last_session = Path(last_session_path)
        if last_session.is_file():
            last_session.unlink()
    except OSError as exc:
        _safe_internal_log(
            "finalize_session_log_persistence failed to remove last_session.log", exc
        )


def export_full_log_to_file(
    runtime_directory: str,
    file_path: str | None = None,
) -> str:
    if not runtime_directory:
        raise ValueError("runtime_directory 不能为空")
    if file_path:
        parent_dir = Path(file_path).parent
        if parent_dir != Path("."):
            parent_dir.mkdir(parents=True, exist_ok=True)
    else:
        logs_dir = _ensure_logs_dir(runtime_directory)
        file_name = datetime.now().strftime("log_%Y%m%d_%H%M%S.txt")
        file_path = str(Path(logs_dir) / file_name)

    session_log_path = get_current_session_log_path()
    if session_log_path and Path(session_log_path).is_file():
        src = str(Path(session_log_path).resolve())
        dst = str(Path(file_path).resolve())
        if Path(src) == Path(dst):
            return file_path
        try:
            shutil.copyfile(src, dst)
            return file_path
        except OSError as exc:
            _safe_internal_log("export_full_log_to_file failed to copy session log", exc)

    Path(file_path).touch()
    return file_path


__all__ = [
    "export_full_log_to_file",
    "finalize_session_log_persistence",
    "get_auto_save_log_settings",
    "get_current_session_log_path",
    "prune_session_log_files",
]
