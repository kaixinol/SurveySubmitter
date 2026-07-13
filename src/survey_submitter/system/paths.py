from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PureWindowsPath

_WINDOWS_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_APP_NAME = "SurveyController"


def is_windows_absolute_path(path: str) -> bool:
    normalized = path.strip()
    return bool(_WINDOWS_DRIVE_ABSOLUTE_RE.match(normalized)) or normalized.startswith(
        ("\\\\", "//")
    )


def normalize_filesystem_path(path: str) -> str:
    raw_path = path.strip()
    expanded = str(Path(raw_path).expanduser()) if raw_path.startswith("~") else raw_path
    if is_windows_absolute_path(expanded):
        return str(PureWindowsPath(expanded))
    return str(Path(expanded).resolve())


def _normalize_path(path: str) -> str:
    return normalize_filesystem_path(path.strip())


def _get_platform_config_root() -> str:
    if sys.platform == "win32":
        return os.environ.get(
            "APPDATA", str(Path("~", "AppData", "Roaming").expanduser())
        )
    elif sys.platform == "darwin":
        return str(Path.home() / "Library" / "Application Support")
    else:
        return os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))


def _get_platform_local_data_root() -> str:
    if sys.platform == "win32":
        return os.environ.get(
            "LOCALAPPDATA", str(Path("~", "AppData", "Local").expanduser())
        )
    return _get_platform_config_root()


def get_roaming_app_data_root() -> str:
    return _get_platform_config_root()


def get_local_app_data_root() -> str:
    return _get_platform_local_data_root()


def get_user_config_root() -> str:
    return str(Path(get_roaming_app_data_root()) / _APP_NAME)


def get_default_user_config_directory() -> str:
    return str(Path(get_user_config_root()) / "configs")


def resolve_user_config_directory(settings=None) -> str:
    if isinstance(settings, str):
        configured_path = settings.strip()
        if configured_path:
            return _normalize_path(str(Path(configured_path).expanduser()))
    if isinstance(settings, dict):
        configured_path = str(settings.get("config_directory", "") or "").strip()
        if configured_path:
            return _normalize_path(str(Path(configured_path).expanduser()))
    return get_default_user_config_directory()


def get_user_config_directory() -> str:
    return resolve_user_config_directory()


def get_user_local_data_root() -> str:
    return str(Path(get_local_app_data_root()) / _APP_NAME)


def get_user_logs_directory() -> str:
    return str(Path(get_user_local_data_root()) / "logs")


def get_user_cache_directory() -> str:
    return str(Path(get_user_local_data_root()) / "cache")


def get_user_updates_directory() -> str:
    return str(Path(get_user_local_data_root()) / "updates")


def get_default_runtime_config_path() -> str:
    return str(Path(get_user_config_root()) / "config.json")


def get_fatal_crash_log_path() -> str:
    return str(Path(get_user_logs_directory()) / "fatal_crash.log")


def get_last_session_log_path() -> str:
    return str(Path(get_user_logs_directory()) / "last_session.log")


def ensure_user_data_directories() -> tuple[str, ...]:
    paths = (
        get_user_config_root(),
        get_user_config_directory(),
        get_user_local_data_root(),
        get_user_logs_directory(),
        get_user_cache_directory(),
        get_user_updates_directory(),
    )
    for path in paths:
        os.makedirs(path, exist_ok=True)
    return tuple(paths)


__all__ = [
    "ensure_user_data_directories",
    "get_default_runtime_config_path",
    "get_fatal_crash_log_path",
    "get_last_session_log_path",
    "get_local_app_data_root",
    "get_roaming_app_data_root",
    "get_user_cache_directory",
    "get_user_config_directory",
    "get_user_config_root",
    "get_user_local_data_root",
    "get_user_logs_directory",
    "get_user_updates_directory",
    "is_windows_absolute_path",
    "normalize_filesystem_path",
    "resolve_user_config_directory",
]
