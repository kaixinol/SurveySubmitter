from __future__ import annotations

import threading
from typing import Callable

_listener_id_counter = 0
_listener_id_lock = threading.Lock()


def _next_listener_id() -> int:
    global _listener_id_counter
    with _listener_id_lock:
        _listener_id_counter += 1
        return _listener_id_counter


class ListenerManager:
    """Manages version-tracked listeners that are notified on buffer updates."""

    def __init__(self) -> None:
        self._version = 0
        self._version_lock = threading.Lock()
        self._listeners: dict[int, Callable[[int], None]] = {}
        self._listeners_lock = threading.Lock()

    # -- version tracking ---------------------------------------------------

    def get_version(self) -> int:
        with self._version_lock:
            return self._version

    def increment_version(self) -> int:
        """Increment and return the new version number."""
        with self._version_lock:
            self._version += 1
            return self._version

    # -- listener registration ----------------------------------------------

    def add_listener(self, listener: Callable[[int], None]) -> int:
        if not callable(listener):
            return 0
        with self._listeners_lock:
            listener_id = _next_listener_id()
            self._listeners[listener_id] = listener
            return listener_id

    def remove_listener(self, listener_id: int) -> None:
        if not listener_id:
            return
        with self._listeners_lock:
            self._listeners.pop(int(listener_id), None)

    # -- notification -------------------------------------------------------

    def notify_listeners(self, version: int) -> None:
        with self._listeners_lock:
            listeners = list(self._listeners.values())
        for listener in listeners:
            try:
                listener(version)
            except Exception as exc:
                from survey_submitter.logging.log_utils import _safe_internal_log

                _safe_internal_log("LogBufferHandler listener failed", exc)
