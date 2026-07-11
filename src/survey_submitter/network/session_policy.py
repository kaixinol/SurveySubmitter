from __future__ import annotations

import asyncio
from typing import Iterable
import logging

from survey_submitter.core.engine.stop_signal import StopSignalLike
from survey_submitter.core.engine.runtime_ui_bridge import RuntimeUiBridge
from survey_submitter.core.task import ExecutionState, ProxyLease
from survey_submitter.constants import PROXY_MAX_PROXIES
from survey_submitter.network.proxy.api import fetch_proxy_batch_async

from survey_submitter.network.proxy.submit_pool import (
    SubmitProxyLease,
    SubmitProxyUnavailableError,
    _blocked_proxy_addresses_locked,
    _discard_unresponsive_proxy,
    _ensure_proxy_pool_deque_locked,
    _mark_proxy_in_use,
    _mark_proxy_temporarily_bad,
    _merge_fetched_proxy_leases_locked,
    _pop_available_proxy_lease_locked,
)
from survey_submitter.network.user_agent import _select_user_agent_for_session

_PROXY_WAIT_POLL_SECONDS = 0.3


def _get_proxy_fetch_async_lock(ctx: ExecutionState) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    current_lock = getattr(ctx, "_proxy_fetch_async_lock", None)
    current_loop = getattr(ctx, "_proxy_fetch_async_lock_loop", None)
    if not isinstance(current_lock, asyncio.Lock) or current_loop is not loop:
        current_lock = asyncio.Lock()
        setattr(ctx, "_proxy_fetch_async_lock", current_lock)
        setattr(ctx, "_proxy_fetch_async_lock_loop", loop)
    return current_lock


def is_proxy_fetch_in_progress(ctx: ExecutionState) -> bool:
    current_lock = getattr(ctx, "_proxy_fetch_async_lock", None)
    return isinstance(current_lock, asyncio.Lock) and current_lock.locked()


def release_proxy_fetch_lock(ctx: ExecutionState) -> None:
    current_lock = getattr(ctx, "_proxy_fetch_async_lock", None)
    if isinstance(current_lock, asyncio.Lock) and current_lock.locked():
        current_lock.release()


def _resolve_proxy_fetch_max_batch_size(ctx: ExecutionState) -> int:
    worker_count = max(1, int(ctx.config.num_threads or 1))
    dynamic_limit = worker_count
    return max(1, min(int(PROXY_MAX_PROXIES or dynamic_limit), dynamic_limit))


def _record_bad_proxy_and_maybe_pause(
    ctx: ExecutionState,
    runtime_bridge: RuntimeUiBridge | None,
) -> bool:

    _ = ctx, runtime_bridge
    return False


def _resolve_proxy_request_num_locked(ctx: ExecutionState) -> int:
    waiting_count = max(1, int(ctx.proxy_waiting_threads or 0))
    active_count = len(ctx.proxy_in_use_by_thread)
    remaining_to_start = max(
        0, int(ctx.config.target_num or 0) - int(ctx.cur_num or 0) - active_count
    )
    if remaining_to_start <= 0:
        return 0
    request_capacity = min(waiting_count, _resolve_proxy_fetch_max_batch_size(ctx))
    return max(1, min(request_capacity, remaining_to_start))


def merge_prefetched_proxy_leases(ctx: ExecutionState, fetched: Iterable[object]) -> int:

    if not fetched:
        return 0
    with ctx.lock:
        before = len(_ensure_proxy_pool_deque_locked(ctx))
        _merge_fetched_proxy_leases_locked(ctx, fetched, select_first=False)
        merged_count = max(0, len(_ensure_proxy_pool_deque_locked(ctx)) - before)
    if merged_count:
        ctx.notify_runtime_change()
    return merged_count


def resolve_proxy_prefetch_request_count(ctx: ExecutionState) -> int:

    if not bool(ctx.config.random_proxy_ip_enabled):
        return 0
    with ctx.lock:
        active_count = len(ctx.proxy_in_use_by_thread)
        remaining_to_start = max(
            0, int(ctx.config.target_num or 0) - int(ctx.cur_num or 0) - active_count
        )
        if remaining_to_start <= 0:
            return 0
        waiting_count = max(0, int(ctx.proxy_waiting_threads or 0))
        if waiting_count <= 0:
            return 0
        target_buffer = min(
            waiting_count, remaining_to_start, _resolve_proxy_fetch_max_batch_size(ctx)
        )
        current_pool_size = len(_ensure_proxy_pool_deque_locked(ctx))
    return max(0, int(target_buffer) - int(current_pool_size))


def should_continue_proxy_prefetch(ctx: ExecutionState) -> bool:

    if not bool(ctx.config.random_proxy_ip_enabled):
        return False
    if _should_stop_proxy_wait(ctx, ctx.stop_event):
        return False
    with ctx.lock:
        active_count = len(ctx.proxy_in_use_by_thread)
        remaining_to_start = max(
            0, int(ctx.config.target_num or 0) - int(ctx.cur_num or 0) - active_count
        )
    return remaining_to_start > 0


def _should_stop_proxy_wait(
    ctx: ExecutionState,
    stop_signal: StopSignalLike | None,
) -> bool:
    if stop_signal is not None and stop_signal.is_set():
        return True
    return bool(ctx.stop_event and ctx.stop_event.is_set())


def _wait_for_next_proxy_cycle(
    ctx: ExecutionState,
    stop_signal: StopSignalLike | None,
    *,
    timeout: float = _PROXY_WAIT_POLL_SECONDS,
) -> bool:
    return ctx.wait_for_runtime_change(stop_signal=stop_signal, timeout=timeout)


async def _wait_for_next_proxy_cycle_async(
    ctx: ExecutionState,
    stop_signal: StopSignalLike | None,
    *,
    timeout: float = _PROXY_WAIT_POLL_SECONDS,
) -> bool:
    return await ctx.wait_for_runtime_change_async(
        stop_signal=stop_signal,
        timeout=timeout,
    )


_PROXY_PREFETCH_IDLE_SECONDS = 0.35


async def wait_for_proxy_prefetch_cycle(
    ctx: ExecutionState,
    stop_signal: StopSignalLike | None,
    *,
    timeout: float = _PROXY_PREFETCH_IDLE_SECONDS,
) -> bool:
    return await _wait_for_next_proxy_cycle_async(ctx, stop_signal, timeout=timeout)


async def _acquire_proxy_fetch_lock_async(
    ctx: ExecutionState,
    stop_signal: StopSignalLike | None,
) -> bool:
    lock = _get_proxy_fetch_async_lock(ctx)
    while not _should_stop_proxy_wait(ctx, stop_signal):
        try:
            await asyncio.wait_for(lock.acquire(), timeout=_PROXY_WAIT_POLL_SECONDS)
            return True
        except asyncio.TimeoutError:
            continue
    return False


async def _select_proxy_for_session_async(
    ctx: ExecutionState,
    thread_name: str = "",
    *,
    stop_signal: StopSignalLike | None = None,
    wait: bool = False,
) -> str | None:
    if not ctx.config.random_proxy_ip_enabled:
        return None
    selected: ProxyLease | None = None
    with ctx.lock:
        selected = _pop_available_proxy_lease_locked(ctx)
    if selected is not None:
        return _mark_proxy_in_use(ctx, thread_name, selected)

    ctx.register_proxy_waiter()
    try:
        while True:
            if _should_stop_proxy_wait(ctx, stop_signal):
                return None
            with ctx.lock:
                selected = _pop_available_proxy_lease_locked(ctx)
            if selected is not None:
                return _mark_proxy_in_use(ctx, thread_name, selected)
            if is_proxy_fetch_in_progress(ctx):
                if await _wait_for_next_proxy_cycle_async(ctx, stop_signal):
                    return None
                continue

            fetch_lock_acquired = await _acquire_proxy_fetch_lock_async(ctx, stop_signal)
            if not fetch_lock_acquired:
                return None
            try:
                with ctx.lock:
                    selected = _pop_available_proxy_lease_locked(ctx)
                    if selected is None:
                        request_num = _resolve_proxy_request_num_locked(ctx)
                    else:
                        request_num = 0
                if selected is not None:
                    return _mark_proxy_in_use(ctx, thread_name, selected)

                if request_num > 0:
                    try:
                        fetched = await fetch_proxy_batch_async(
                            expected_count=request_num,
                            stop_signal=ctx.stop_event,
                        )
                    except (RuntimeError, OSError) as exc:
                        logging.warning(
                            f"\u83b7\u53d6\u968f\u673a\u4ee3\u7406\u5931\u8d25\uff1a{exc}"
                        )
                        fetched = None
                    if fetched:
                        with ctx.lock:
                            selected = _merge_fetched_proxy_leases_locked(
                                ctx, fetched, select_first=True
                            )
                        if selected is not None:
                            return _mark_proxy_in_use(ctx, thread_name, selected)
            finally:
                release_proxy_fetch_lock(ctx)

            if not wait:
                return None
            if await _wait_for_next_proxy_cycle_async(ctx, stop_signal):
                return None
    finally:
        ctx.unregister_proxy_waiter()


def _resolve_proxy_provider_for_thread(ctx: ExecutionState, thread_name: str) -> str:
    if not thread_name:
        return "unknown"
    try:
        with ctx.lock:
            lease = ctx.proxy_in_use_by_thread.get(thread_name)
            if lease is None:
                return "unknown"
            return str(lease.source or "unknown").strip() or "unknown"
    except (AttributeError, KeyError):
        logging.info("\u8bfb\u53d6\u4ee3\u7406\u6765\u6e90\u5931\u8d25", exc_info=True)
    return "unknown"


async def acquire_submit_proxy(
    ctx: ExecutionState,
    thread_name: str = "",
    *,
    stop_signal: StopSignalLike | None = None,
    wait: bool = True,
) -> SubmitProxyLease:
    proxy_address = await _select_proxy_for_session_async(
        ctx,
        thread_name,
        stop_signal=stop_signal,
        wait=wait,
    )
    provider = _resolve_proxy_provider_for_thread(ctx, thread_name) if proxy_address else "unknown"
    return SubmitProxyLease(address=proxy_address, provider=provider)


def release_submit_proxy(ctx: ExecutionState, thread_name: str, proxy_address: str | None) -> None:
    if not proxy_address or not thread_name:
        return
    try:
        ctx.release_proxy_in_use(thread_name)
    except (KeyError, AttributeError):
        logging.info("\u91ca\u653e\u63d0\u4ea4\u4ee3\u7406\u5360\u7528\u5931\u8d25", exc_info=True)


def mark_submit_proxy_success(ctx: ExecutionState, proxy_address: str | None) -> None:
    if not proxy_address:
        return
    try:
        ctx.mark_successful_proxy_address(proxy_address)
    except (AttributeError, KeyError):
        logging.info(
            "\u8bb0\u5f55\u6210\u529f\u4ee3\u7406\u5931\u8d25\uff1a%s", proxy_address, exc_info=True
        )
