"""Minimal JSON-based application settings."""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from survey_submitter.system.paths import get_user_config_root

_SETTINGS_FILE_NAME = "app_settings.json"
_settings_lock = threading.RLock()
_settings_cache: dict | None = None


def _settings_file_path() -> str:
    return os.path.join(get_user_config_root(), _SETTINGS_FILE_NAME)


def _load_settings() -> dict:
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    path = _settings_file_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _settings_cache = json.load(f)
            return _settings_cache
        except (json.JSONDecodeError, OSError):
            pass
    _settings_cache = {}
    return _settings_cache


class _JsonSettings:
    """Key-value settings interface backed by a JSON file."""

    def value(self, key: str, default: Any = None) -> Any:
        with _settings_lock:
            store = _load_settings()
        result = store.get(key, default)
        return result

    def setValue(self, key: str, value: Any) -> None:
        with _settings_lock:
            store = _load_settings()
            store[key] = value
            self._flush(store)

    def remove(self, key: str) -> None:
        with _settings_lock:
            store = _load_settings()
            store.pop(key, None)
            self._flush(store)

    def sync(self) -> None:
        with _settings_lock:
            store = _load_settings()
            self._flush(store)

    @staticmethod
    def _flush(store: dict) -> None:
        path = _settings_file_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)


def app_settings() -> _JsonSettings:
    return _JsonSettings()


def get_bool_setting(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value) if value is not None else default


def get_int_setting(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
