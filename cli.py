"""SurveyController CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import io

_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

_FAULT_HANDLER_STREAM: Optional[io.IOBase] = None
_ORIG_STDOUT: Optional[io.TextIOBase] = None

_TYPE_LABELS = {
    "single": "单选",
    "multiple": "多选",
    "text": "填空",
    "multi_text": "多项填空",
    "matrix": "矩阵",
    "scale": "量表",
    "score": "评分",
    "dropdown": "下拉",
    "slider": "滑块",
    "unknown": "未知",
}


def _enable_fault_handler() -> None:
    global _FAULT_HANDLER_STREAM
    if faulthandler.is_enabled():
        return
    try:
        from survey_submitter.system.paths import get_fatal_crash_log_path

        fault_log_path = get_fatal_crash_log_path()
        logs_dir = Path(fault_log_path).parent
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


def _out() -> io.TextIOBase:
    return _ORIG_STDOUT or sys.stdout  # type: ignore[return-value]


def _type_label(type_code: str) -> str:
    return _TYPE_LABELS.get(type_code, type_code)


def _print_survey(definition: object) -> None:
    from survey_submitter.providers.contracts import (
        SurveyDefinition,
        ChoiceQuestionMeta,
    )

    out = _out()
    defn: SurveyDefinition = definition  # type: ignore[assignment]
    out.write(f"问卷标题: {defn.title}\n")
    out.write(f"平台: {defn.provider}\n")
    out.write(f"题目数量: {len(defn.questions)}\n")
    out.write("-" * 60 + "\n")

    for q in defn.questions:
        label = _type_label(q.type_code.value)
        required_mark = " *" if q.required else ""
        out.write(f"\n第{q.num}题 [{label}]{required_mark}\n")
        out.write(f"  {q.title}\n")

        if q.has_jump:
            rules = q.jump_rules or []
            for rule in rules:
                target = rule.get("jumpto", "?") if isinstance(rule, dict) else "?"
                out.write(f"  → 跳题: 跳到第{target}题\n")

        if q.has_display_condition:
            conditions = q.display_conditions or []
            for cond in conditions:
                if isinstance(cond, dict):
                    src = cond.get("condition_question_num", "?")
                    mode = cond.get("condition_mode", "selected")
                    opts = cond.get("condition_option_indices", [])
                    out.write(f"  → 显隐条件: 第{src}题 {mode} 选项{opts}\n")

        if isinstance(q, ChoiceQuestionMeta) and q.option_texts:
            fillable = set(q.fillable_options or [])
            for i, text in enumerate(q.option_texts):
                fill_tag = " [可填空]" if i in fillable else ""
                out.write(f"  {i + 1}. {text}{fill_tag}\n")

        if q.unsupported:
            reason = q.unsupported_reason or "不支持"
            out.write(f"  ⚠ {reason}\n")


async def _cmd_parse_url(url: str) -> None:
    from survey_submitter.providers.registry import parse_survey

    definition = await parse_survey(url)
    _print_survey(definition)


def _cmd_dry_run(config_path: str) -> None:
    from survey_submitter.core.config.yaml_loader import load_yaml_config
    from survey_submitter.core.engine.execution_builder import prepare_execution_artifacts
    from survey_submitter.providers.registry import parse_survey

    out = _out()
    out.write(f"[dry-run] 加载配置: {config_path}\n")
    config = load_yaml_config(config_path)

    if not config.survey.url:
        raise ValueError("配置文件中未指定问卷 URL")

    definition = asyncio.run(parse_survey(config.survey.url))
    config.answer_config.questions_info = definition.questions
    config.survey.survey_title = config.survey.survey_title or definition.title
    config.survey.survey_provider = definition.provider

    if not config.answer_config.question_entries:
        from survey_submitter.core.questions.config import build_default_question_entries

        config.answer_config.question_entries = build_default_question_entries(
            definition.questions,
            survey_url=config.survey.url,
        )

    out.write(f"[dry-run] 问卷解析成功: {definition.title} ({len(definition.questions)} 题)\n")

    artifacts = prepare_execution_artifacts(config)
    exec_config = artifacts.execution_config_template

    out.write(f"[dry-run] 执行配置验证通过\n")
    out.write(f"  目标份数: {exec_config.target_num}\n")
    out.write(f"  并发线程: {exec_config.num_threads}\n")
    out.write(f"  提交间隔: {exec_config.submit_interval_range_seconds}s\n")
    out.write(f"  答题时长: {exec_config.answer_duration_range_seconds}s\n")
    out.write(f"[dry-run] 全部检查通过，可以正式运行\n")


def _cmd_run(config_path: str) -> None:
    from survey_submitter.core.engine.headless_runner import HeadlessRunner

    runner = HeadlessRunner(config_path)

    loop = asyncio.new_event_loop()

    def _handle_signal() -> None:
        runner.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        loop.run_until_complete(runner.run())
    except KeyboardInterrupt:
        runner.request_stop()
    finally:
        loop.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="survey", description="SurveyController CLI")
    parser.add_argument("config", nargs="?", help="YAML 配置文件路径")
    parser.add_argument("--url", help="直接解析问卷链接")
    parser.add_argument("--dry-run", action="store_true", help="仅解析问卷并验证配置，不提交")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args()

    if not args.url and not args.config:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    global _ORIG_STDOUT
    _ORIG_STDOUT = sys.stdout

    bootstrap()

    try:
        if args.url:
            asyncio.run(_cmd_parse_url(args.url))
        elif args.dry_run:
            _cmd_dry_run(args.config)
        else:
            _cmd_run(args.config)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()


if __name__ == "__main__":
    main()
