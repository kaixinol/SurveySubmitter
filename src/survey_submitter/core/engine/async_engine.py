from __future__ import annotations

import asyncio
import concurrent.futures
from loguru import logger
import threading
from typing import Any, Coroutine

from importlib.metadata import version as pkg_version

from survey_submitter.core.engine.async_events import AsyncRunContext
from survey_submitter.core.engine.async_runtime_loop import AsyncSlotRunner
from survey_submitter.core.engine.async_scheduler import AsyncScheduler
from survey_submitter.core.engine.async_status_bus import AsyncStatusBus
from survey_submitter.core.task import ExecutionConfig, ExecutionState
from survey_submitter.network.proxy.api import fetch_proxy_batch_async
import survey_submitter.network.http as http_client
from survey_submitter.network.session_policy import (
    _acquire_proxy_fetch_lock_async,
    merge_prefetched_proxy_leases,
    release_proxy_fetch_lock,
    resolve_proxy_prefetch_request_count,
    should_continue_proxy_prefetch,
    wait_for_proxy_prefetch_cycle,
)
from survey_submitter.providers.registry import parse_survey


def _format_seconds_range(value: tuple[int, int] | list[int] | None) -> str:
    try:
        if value is None:
            return "未知"
        start, end = value
        return f"{int(start)}-{int(end)}秒"
    except (ValueError, TypeError):
        return "未知"


def _format_proxy_source(source: str | None) -> str:
    normalized = str(source or "custom").strip().lower()
    labels = {
        "custom": "自定义",
    }
    return labels.get(normalized, normalized or "自定义")


async def _run_proxy_prefetch(
    state: ExecutionState,
    stop_event: asyncio.Event,
) -> None:
    while should_continue_proxy_prefetch(state) and not stop_event.is_set():
        request_count = resolve_proxy_prefetch_request_count(state)
        if request_count <= 0:
            if await wait_for_proxy_prefetch_cycle(state, state.stop_event):
                return
            continue
        fetch_lock_acquired = False
        try:
            fetch_lock_acquired = await _acquire_proxy_fetch_lock_async(state, state.stop_event)
            if not fetch_lock_acquired or state.stop_event.is_set() or stop_event.is_set():
                return
            request_count = resolve_proxy_prefetch_request_count(state)
            if request_count <= 0:
                continue
            fetched = await fetch_proxy_batch_async(
                expected_count=request_count,
                stop_signal=state.stop_event,
            )
            if state.stop_event.is_set() or stop_event.is_set():
                return
            merge_prefetched_proxy_leases(state, fetched)
        except (http_client.TransportError, OSError, TimeoutError):
            logger.opt(exception=True).debug("随机IP异步预热失败")
        finally:
            if fetch_lock_acquired:
                try:
                    release_proxy_fetch_lock(state)
                except Exception:
                    logger.opt(exception=True).debug("释放随机IP异步预热锁失败")
        if await wait_for_proxy_prefetch_cycle(state, state.stop_event):
            return


class AsyncRuntimeEngine:
    def __init__(self, *, status_bus: AsyncStatusBus | None = None) -> None:
        self._status_bus = status_bus or AsyncStatusBus()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        self._start_lock = threading.Lock()
        self._run_future: concurrent.futures.Future[Any] | None = None
        self._stop_event: asyncio.Event | None = None
        self._pause_event: asyncio.Event | None = None
        self._closed = False
        self._state: ExecutionState | None = None

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def start(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._loop_ready.clear()

            def _runner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                self._loop_ready.set()
                try:
                    loop.run_forever()
                finally:
                    pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    loop.close()

            self._thread = threading.Thread(target=_runner, daemon=True, name="AsyncRuntimeEngine")
            self._thread.start()
        self._loop_ready.wait()

    def _submit(self, coro: Coroutine[Any, Any, Any]) -> concurrent.futures.Future[Any]:
        self.start()
        if self._loop is None:
            raise RuntimeError("AsyncRuntimeEngine loop 未启动")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def start_run(
        self,
        *,
        config: ExecutionConfig,
        state: ExecutionState,
    ) -> concurrent.futures.Future[Any]:
        if self._run_future is not None and not self._run_future.done():
            raise RuntimeError("任务已在运行中")
        future = self._submit(self._run(config=config, state=state))
        self._run_future = future
        return future

    async def _run(
        self,
        *,
        config: ExecutionConfig,
        state: ExecutionState,
    ) -> None:
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._state = state
        state.stop_event.clear()
        worker_count = max(1, int(config.num_threads or 1))
        state.ensure_worker_threads(worker_count, prefix="Slot")
        scheduler = AsyncScheduler(concurrency=worker_count)
        run_context = AsyncRunContext(
            state=state,
            stop_event=self._stop_event,
            pause_event=self._pause_event,
            status_sink=self._status_bus.emit,
        )
        logger.info(
            f"任务启动：版本={pkg_version('surveysubmitter')} 问卷链接={config.url or ''} 平台={config.survey_provider or ''} 目标份数={config.target_num} 当前进度={state.cur_num}/{config.target_num} 并发数={worker_count} 作答时长={_format_seconds_range(config.answer_duration_range_seconds)} 随机IP={'开启' if config.random_proxy_ip else '关闭'} 代理源={_format_proxy_source(config.proxy_source)} 运行时=纯HTTP"
        )
        stop_event = self._stop_event
        if stop_event is None:
            raise RuntimeError("AsyncRuntimeEngine stop_event 未初始化")
        prefetch_task = asyncio.create_task(
            _run_proxy_prefetch(state, stop_event),
            name="AsyncProxyPrefetch",
        )
        try:
            await self._run_slots(
                worker_count, config, state, run_context, scheduler
            )
        except* Exception as exc_group:
            errors = [
                exc for exc in exc_group.exceptions if not isinstance(exc, asyncio.CancelledError)
            ]
            if not errors:
                raise
            if len(errors) == 1:
                raise errors[0]
            raise ExceptionGroup("AsyncRuntimeEngine slot 运行失败", errors)
        finally:
            prefetch_task.cancel()
            await asyncio.gather(prefetch_task, return_exceptions=True)
            if self._stop_event is not None:
                self._stop_event.set()
            await scheduler.close()
            state.stop_event.set()
            self._stop_event = None
            self._pause_event = None
            self._state = None

    async def _run_slots(
        self,
        worker_count: int,
        config: ExecutionConfig,
        state: ExecutionState,
        run_context: AsyncRunContext,
        scheduler: AsyncScheduler,
    ) -> None:
        async with asyncio.TaskGroup() as task_group:
            for slot_index in range(worker_count):
                task_group.create_task(
                    AsyncSlotRunner(
                        slot_id=slot_index + 1,
                        config=config,
                        state=state,
                        run_context=run_context,
                        scheduler=scheduler,
                    ).run(),
                    name=f"AsyncSlotRunner-{slot_index + 1}",
                )

    def stop_run(self) -> None:
        stop_event = self._stop_event
        state = self._state
        if state is not None:
            state.stop_event.set()
        if self._loop is not None and stop_event is not None:
            self._loop.call_soon_threadsafe(stop_event.set)
        future = self._run_future
        if future is not None and future.done():
            self._run_future = None

    def pause_run(self, reason: str = "") -> None:
        del reason
        pause_event = self._pause_event
        if self._loop is not None and pause_event is not None:
            self._loop.call_soon_threadsafe(pause_event.set)

    def resume_run(self) -> None:
        pause_event = self._pause_event
        if self._loop is not None and pause_event is not None:
            self._loop.call_soon_threadsafe(pause_event.clear)

    def parse_survey(self, url: str) -> concurrent.futures.Future[Any]:
        return self._submit(parse_survey(url))

    def shutdown(self, *, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop_run()
        future = self._run_future
        if future is not None:
            try:
                future.result(timeout=max(0.0, float(timeout or 0.0)))
            except Exception:
                logger.opt(exception=True).debug("AsyncRuntimeEngine shutdown 等待运行结束失败")
        loop = self._loop
        thread = self._thread
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout or 0.0)))
        self._thread = None
        self._loop = None


__all__ = ["AsyncRuntimeEngine"]
