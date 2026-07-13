from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_NAME = "SurveyController"


def _get_platform_config_root() -> str:
    if sys.platform == "win32":
        return os.environ.get(
            "APPDATA", str(Path("~", "AppData", "Roaming").expanduser())
        )
    if sys.platform == "darwin":
        return str(Path.home() / "Library" / "Application Support")
    return os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))


def _get_platform_local_data_root() -> str:
    if sys.platform == "win32":
        return os.environ.get(
            "LOCALAPPDATA", str(Path("~", "AppData", "Local").expanduser())
        )
    return _get_platform_config_root()


def get_user_config_root() -> str:
    return str(Path(_get_platform_config_root()) / _APP_NAME)


def get_default_user_config_directory() -> str:
    return str(Path(get_user_config_root()) / "configs")


def resolve_user_config_directory(settings=None) -> str:
    if isinstance(settings, str):
        configured_path = settings.strip()
        if configured_path:
            return str(Path(configured_path).expanduser().resolve())
    if isinstance(settings, dict):
        configured_path = str(settings.get("config_directory", "") or "").strip()
        if configured_path:
            return str(Path(configured_path).expanduser().resolve())
    return get_default_user_config_directory()


def get_user_config_directory() -> str:
    return resolve_user_config_directory()


def get_user_local_data_root() -> str:
    return str(Path(_get_platform_local_data_root()) / _APP_NAME)


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
        Path(path).mkdir(parents=True, exist_ok=True)
    return tuple(paths)


__all__ = [
    "ensure_user_data_directories",
    "get_default_runtime_config_path",
    "get_default_user_config_directory",
    "get_fatal_crash_log_path",
    "get_last_session_log_path",
    "get_user_cache_directory",
    "get_user_config_directory",
    "get_user_config_root",
    "get_user_local_data_root",
    "get_user_logs_directory",
    "get_user_updates_directory",
    "resolve_user_config_directory",
]
