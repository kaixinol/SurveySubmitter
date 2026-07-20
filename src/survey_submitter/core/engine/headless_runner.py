"""Headless runner for CLI-based survey submission.

Replaces the GUI RunController: loads a YAML config, parses the survey
from the URL, builds execution artifacts, starts the AsyncRuntimeEngine,
and polls progress until completion or stop signal.
"""

from __future__ import annotations

import asyncio
from loguru import logger

from survey_submitter.core.config.yaml_loader import load_yaml_config
from survey_submitter.core.engine.async_engine import AsyncRuntimeEngine
from survey_submitter.core.engine.execution_builder import prepare_execution_artifacts
from survey_submitter.core.questions.default_builder import build_default_question_entries
from survey_submitter.core.task.task_context import ExecutionState
from survey_submitter.providers.contracts import SurveyDefinition
from survey_submitter.providers.registry import parse_survey


class HeadlessRunner:
    """CLI-based survey runner replacing the GUI RunController."""

    def __init__(self, config_path: str, *, parse_only: bool = False) -> None:
        self._config_path = config_path
        self._parse_only = parse_only
        self._engine: AsyncRuntimeEngine | None = None
        self._stop_requested = False

    def request_stop(self) -> None:
        """Signal the runner to stop. Safe to call from signal handlers."""
        self._stop_requested = True
        if self._engine is not None:
            self._engine.stop_run()

    async def _parse_survey(self, url: str) -> SurveyDefinition:
        """Fetch and parse the survey from its URL."""
        logger.info(f"正在从网页解析问卷: {url}")
        definition = await parse_survey(url)
        logger.info(
            f"解析完成: 标题={definition.title}, 题目数={len(definition.questions)}, 平台={definition.provider}"
        )
        return definition

    @staticmethod
    def _log_parsed_questions(definition: SurveyDefinition) -> None:
        """Output parsed question details for --parse-only mode."""
        from survey_submitter.core.questions.types import TypeCode

        for q in definition.questions:
            if q.type_code == TypeCode.DESCRIPTION:
                continue
            unsupported = " [不支持]" if q.unsupported else ""
            location = " [地址题]" if q.type_code == TypeCode.LOCATION else ""
            logger.info(
                f"  Q{q.num}: {q.title[:60]} ({q.type_code}){location}{unsupported}"
            )

    async def run(self) -> None:
        """Load config, parse survey, prepare artifacts, and run."""
        logger.info(f"加载配置文件: {self._config_path}")
        config = load_yaml_config(self._config_path)

        if not config.survey.url:
            raise ValueError("配置文件中未指定问卷 URL")

        definition = await self._parse_survey(config.survey.url)

        config.survey.survey_title = config.survey.survey_title or definition.title
        config.survey.survey_provider = definition.provider

        if not config.answer_config.question_entries:
            logger.info("未配置题目权重，自动生成默认配置")
            config.answer_config.question_entries = build_default_question_entries(
                definition.questions,
                survey_url=config.survey.url,
            )

        if self._parse_only:
            self._log_parsed_questions(definition)
            logger.info(
                f"问卷解析完成 (--parse-only 模式，共 {len(definition.questions)} 题，不提交)"
            )
            return

        artifacts = prepare_execution_artifacts(
            config,
            questions_info=definition.questions,
        )
        exec_config = artifacts.execution_config_template
        state = ExecutionState(config=exec_config)

        self._engine = AsyncRuntimeEngine()
        self._engine.start()

        try:
            logger.info(
                f"开始提交: 目标={exec_config.target_num}, 并发={exec_config.num_threads}"
            )

            future = self._engine.start_run(
                config=exec_config,
                state=state,
            )

            while not future.done():
                await asyncio.sleep(5)
                success = state.cur_num
                fail = state.cur_fail
                target = exec_config.target_num
                logger.info(f"进度: {success}/{target} 成功, {fail} 失败")

                if self._stop_requested:
                    logger.info("正在停止...")
                    break

            if future.done():
                exc = future.exception()
                if exc:
                    logger.error(f"运行出错: {exc}")
                else:
                    logger.info(
                        f"运行完成: {state.cur_num}/{exec_config.target_num} 成功, {state.cur_fail} 失败"
                    )
        finally:
            self._engine.shutdown()
            logger.info("引擎已关闭")


__all__ = ["HeadlessRunner"]
