"""SurveyController CLI entry point."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="survey", description="SurveyController CLI")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run survey submission")
    run_parser.add_argument("config", help="Path to YAML config file")
    run_parser.add_argument("--parse-only", action="store_true", help="Only parse survey, don't submit")
    run_parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "run":
        from software.core.engine.headless_runner import HeadlessRunner
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


if __name__ == "__main__":
    main()
