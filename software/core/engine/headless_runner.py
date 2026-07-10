"""Headless runner for CLI-based survey submission.

Replaces the GUI RunController: loads a YAML config, builds execution
artifacts via the execution_builder, starts the AsyncRuntimeEngine,
and polls progress until completion or stop signal.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from software.core.config.yaml_loader import load_yaml_config
from software.core.engine.async_engine import AsyncRuntimeEngine
from software.core.engine.execution_builder import prepare_execution_artifacts
from software.core.engine.stop_signal import StopSignalLike
from software.core.task.task_context import ExecutionConfig, ExecutionState

logger = logging.getLogger(__name__)


class _HeadlessControlPort:
    """No-op RuntimeControlPort for headless operation.

    Implements the RuntimeControlPort protocol with no-op methods
    since there is no GUI to update.
    """

    def wait_if_paused(self, stop_signal: Optional[StopSignalLike] = None) -> None:
        pass

    def on_random_ip_submission(self, stop_signal: Optional[StopSignalLike] = None) -> None:
        pass

    def on_random_ip_loading_changed(self, loading: bool, message: str = "") -> None:
        pass


class HeadlessRunner:
    """CLI-based survey runner replacing the GUI RunController."""

    def __init__(self, config_path: str, *, parse_only: bool = False) -> None:
        self._config_path = config_path
        self._parse_only = parse_only
        self._engine: Optional[AsyncRuntimeEngine] = None
        self._stop_requested = False

    def request_stop(self) -> None:
        """Signal the runner to stop. Safe to call from signal handlers."""
        self._stop_requested = True
        if self._engine is not None:
            self._engine.stop_run()

    async def run(self) -> None:
        """Load config, prepare artifacts, and run the survey engine."""
        logger.info("加载配置文件: %s", self._config_path)
        config = load_yaml_config(self._config_path)

        logger.info("解析问卷: %s", config.url)
        artifacts = prepare_execution_artifacts(config)

        if self._parse_only:
            logger.info("问卷解析完成 (--parse-only 模式，不提交)")
            return

        exec_config = artifacts.execution_config_template
        state = ExecutionState(config=exec_config)

        self._engine = AsyncRuntimeEngine()
        self._engine.start()

        try:
            logger.info(
                "开始提交: 目标=%d, 并发=%d",
                exec_config.target_num,
                exec_config.num_threads,
            )

            future = self._engine.start_run(
                config=exec_config,
                state=state,
                runtime_bridge=_HeadlessControlPort(),
            )

            # Progress logging loop
            while not future.done():
                await asyncio.sleep(5)
                success = state.cur_num
                fail = state.cur_fail
                target = exec_config.target_num
                logger.info("进度: %d/%d 成功, %d 失败", success, target, fail)

                if self._stop_requested:
                    logger.info("正在停止...")
                    break

            if future.done():
                exc = future.exception()
                if exc:
                    logger.error("运行出错: %s", exc)
                else:
                    logger.info(
                        "运行完成: %d/%d 成功, %d 失败",
                        state.cur_num,
                        exec_config.target_num,
                        state.cur_fail,
                    )
        finally:
            self._engine.shutdown()
            logger.info("引擎已关闭")


__all__ = ["HeadlessRunner"]
