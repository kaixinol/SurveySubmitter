"""SurveyController CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import logging
import os
import signal
import sys
from typing import Optional

_FAULT_HANDLER_STREAM: Optional[object] = None


def _enable_fault_handler() -> None:
    global _FAULT_HANDLER_STREAM
    if faulthandler.is_enabled():
        return
    try:
        from survey_submitter.system.paths import get_fatal_crash_log_path

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


def bootstrap() -> None:
    from survey_submitter.system.paths import ensure_user_data_directories
    import survey_submitter.network.http as http_client
    from survey_submitter.logging.log_utils import setup_logging as _setup_logging

    ensure_user_data_directories()
    _enable_fault_handler()
    _setup_logging()
    http_client.prewarm()


def shutdown() -> None:
    try:
        from survey_submitter.logging.log_utils import shutdown_logging

        shutdown_logging()
    except Exception:
        pass
    _disable_fault_handler()


def main() -> None:
    parser = argparse.ArgumentParser(prog="survey", description="SurveyController CLI")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run survey submission")
    run_parser.add_argument("config", help="Path to YAML config file")
    run_parser.add_argument(
        "--parse-only", action="store_true", help="Only parse survey, don't submit"
    )
    run_parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    bootstrap()

    try:
        if args.command == "run":
            from survey_submitter.core.engine.headless_runner import HeadlessRunner

            runner = HeadlessRunner(args.config, parse_only=args.parse_only)

            loop = asyncio.new_event_loop()

            def _handle_signal():
                runner.request_stop()

            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _handle_signal)

            try:
                loop.run_until_complete(runner.run())
            except KeyboardInterrupt:
                runner.request_stop()
            finally:
                loop.close()
    finally:
        shutdown()


if __name__ == "__main__":
    main()
