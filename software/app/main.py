from __future__ import annotations

import faulthandler
import logging
import os
import sys
from typing import Optional

from software.app.user_paths import (
    ensure_user_data_directories,
    get_fatal_crash_log_path,
)
import software.network.http as http_client
from software.logging.log_utils import setup_logging as _setup_logging

_FAULT_HANDLER_STREAM: Optional[object] = None


def _enable_fault_handler() -> None:
    """Enable the Python fault handler, directing output to the crash log if possible."""
    global _FAULT_HANDLER_STREAM

    if faulthandler.is_enabled():
        return

    try:
        fault_log_path = get_fatal_crash_log_path()
        logs_dir = os.path.dirname(fault_log_path)
        os.makedirs(logs_dir, exist_ok=True)
        _FAULT_HANDLER_STREAM = open(fault_log_path, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(_FAULT_HANDLER_STREAM, all_threads=True)
    except Exception:
        try:
            faulthandler.enable(all_threads=True)
        except Exception:
            _FAULT_HANDLER_STREAM = None


def _disable_fault_handler() -> None:
    global _FAULT_HANDLER_STREAM

    try:
        if faulthandler.is_enabled():
            faulthandler.disable()
    except Exception:
        pass

    stream = _FAULT_HANDLER_STREAM
    _FAULT_HANDLER_STREAM = None
    if stream is not None:
        try:
            stream.close()
        except Exception:
            pass


def setup_logging() -> None:
    """Configure application logging."""
    _setup_logging()


def prewarm_runtime() -> None:
    """Pre-warm HTTP connection pools and other runtime resources."""
    http_client.prewarm()


def bootstrap() -> None:
    """Run common bootstrap steps (directories, fault handler, logging, HTTP prewarm).

    This is intended to be called by the real entry point (e.g. ``cli.py``)
    before any application logic runs.
    """
    ensure_user_data_directories()
    _enable_fault_handler()
    setup_logging()
    prewarm_runtime()


def shutdown() -> None:
    """Gracefully shut down logging and fault handler."""
    try:
        from software.logging.log_utils import shutdown_logging
        shutdown_logging()
    except Exception:
        pass
    _disable_fault_handler()


if __name__ == "__main__":
    # main.py is no longer the application entry point.
    # Use software.cli (or the ``cli.py`` script) instead.
    print(
        "software.app.main is no longer the application entry point.\n"
        "Use 'python -m software.cli' or the cli.py script instead.",
        file=sys.stderr,
    )
    raise SystemExit(1)
