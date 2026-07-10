from __future__ import annotations

import os
import sys
from typing import Optional, Union

from software.app.path_utils import normalize_filesystem_path

_APP_NAME = "SurveyController"


def _normalize_path(path: str) -> str:
    return normalize_filesystem_path(str(path or "").strip())


def _get_platform_config_root() -> str:
    """Return the platform-specific base directory for application config/data."""
    if sys.platform == "win32":
        return os.environ.get("APPDATA", os.path.expanduser(os.path.join("~", "AppData", "Roaming")))
    elif sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        return os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))


def _get_platform_local_data_root() -> str:
    """Return the platform-specific base directory for local (non-roaming) data."""
    if sys.platform == "win32":
        return os.environ.get("LOCALAPPDATA", os.path.expanduser(os.path.join("~", "AppData", "Local")))
    # On macOS and Linux, local data lives in the same place as roaming config.
    return _get_platform_config_root()


def get_roaming_app_data_root() -> str:
    """Return the base directory for roaming application data."""
    return _get_platform_config_root()


def get_local_app_data_root() -> str:
    """Return the base directory for local (non-roaming) application data."""
    return _get_platform_local_data_root()


def get_user_config_root() -> str:
    """Return the application-specific config root directory."""
    return os.path.join(get_roaming_app_data_root(), _APP_NAME)


def get_default_user_config_directory() -> str:
    """Return the default directory for user configuration files."""
    return os.path.join(get_user_config_root(), "configs")


def resolve_user_config_directory(settings=None) -> str:
    """Resolve the user config directory.

    Without Qt/QSettings, this returns the default directory.  If *settings*
    is a plain string it is treated as an explicit override path.  If it is a
    dict, the key ``config_directory`` is consulted.
    """
    if isinstance(settings, str):
        configured_path = settings.strip()
        if configured_path:
            return _normalize_path(os.path.expanduser(configured_path))
    if isinstance(settings, dict):
        configured_path = str(settings.get("config_directory", "") or "").strip()
        if configured_path:
            return _normalize_path(os.path.expanduser(configured_path))
    return get_default_user_config_directory()


def get_user_config_directory() -> str:
    """Return the resolved user config directory (using defaults)."""
    return resolve_user_config_directory()


def get_user_local_data_root() -> str:
    """Return the application-specific local data root."""
    return os.path.join(get_local_app_data_root(), _APP_NAME)


def get_user_logs_directory() -> str:
    """Return the directory for log files."""
    return os.path.join(get_user_local_data_root(), "logs")


def get_user_cache_directory() -> str:
    """Return the directory for cached data."""
    return os.path.join(get_user_local_data_root(), "cache")


def get_user_updates_directory() -> str:
    """Return the directory for update packages."""
    return os.path.join(get_user_local_data_root(), "updates")


def get_default_runtime_config_path() -> str:
    """Return the path to the default runtime configuration file."""
    return os.path.join(get_user_config_root(), "config.json")


def get_fatal_crash_log_path() -> str:
    """Return the path to the fatal crash log file."""
    return os.path.join(get_user_logs_directory(), "fatal_crash.log")


def get_last_session_log_path() -> str:
    """Return the path to the last-session log file."""
    return os.path.join(get_user_logs_directory(), "last_session.log")


def ensure_user_data_directories() -> tuple[str, ...]:
    """Create (if needed) all standard user data directories and return their paths."""
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
    "get_default_user_config_directory",
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
    "resolve_user_config_directory",
]
