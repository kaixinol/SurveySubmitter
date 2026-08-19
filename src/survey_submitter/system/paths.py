from __future__ import annotations

import sys
from pathlib import Path

_APP_NAME = "SurveyController"


def _get_platform_root() -> str:
    if sys.platform == "darwin":
        return str(Path.home() / "Library" / "Application Support")
    return str(Path.home() / ".config")


def get_user_local_data_root() -> str:
    return str(Path(_get_platform_root()) / _APP_NAME)


def get_user_logs_directory() -> str:
    return str(Path(get_user_local_data_root()) / "logs")


def _get_repo_root() -> str:
    return str(Path(__file__).resolve().parent.parent.parent.parent)


def get_resource_path(relative_path: str) -> str:
    return str(Path(_get_repo_root()) / relative_path)


__all__ = [
    "get_user_logs_directory",
    "get_resource_path",
]
