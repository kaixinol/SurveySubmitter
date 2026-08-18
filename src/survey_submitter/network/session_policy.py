from __future__ import annotations

import asyncio
from typing import Iterable

from loguru import logger

from survey_submitter.constants import PROXY_MAX_PROXIES
from survey_submitter.core.engine.stop_signal import StopSignalLike
from survey_submitter.core.task import ExecutionState, ProxyLease
from survey_submitter.logging.log_utils import (
    log_deduped_message,
    reset_deduped_log_message,
)
from survey_submitter.network.proxy.api import (
    ProxyApiNotConfiguredError,
    fetch_proxy_batch_async,
)
from survey_submitter.network.proxy.submit import (  # noqa: F401  — re-exported for other modules
    SubmitProxyLease,
    SubmitProxyUnavailableError,  # noqa: F401  — re-exported for other modules
    _discard_unresponsive_proxy,
    _ensure_proxy_pool_deque_locked,
    _mark_proxy_in_use,
    _mark_proxy_temporarily_bad,
    _merge_fetched_proxy_leases_locked,
    _pop_available_proxy_lease_locked,
    _return_proxy_lease_to_pool,
)
from survey_submitter.network.user_agent import (  # noqa: F401  — re-exported for other modules
    _select_user_agent_for_session,
)

_PROXY_WAIT_POLL_SECONDS = 0.3
_PROXY_FETCH_FAILED_DEDUP_KEY = "random_proxy_fetch_failed"
_LOCAL_PROXY_EXHAUSTED_MESSAGE = "本地静态代理池已耗尽，无法获取可用代理"


def _stop_run_for_proxy_api_not_configured(ctx: ExecutionState, exc: BaseException) -> None:
    message = str(exc or "").strip() or "自定义代理API地址未配置，请在设置中填写API地址"
    log_deduped_message(_PROXY_FETCH_FAILED_DEDUP_KEY, f"获取随机代理失败：{message}", level="WARNING")
    ctx.mark_terminal_stop(
        "proxy_api_not_configured",
        failure_reason="proxy_unavailable",
        message=message,
    )
    ctx.stop_event.set()


def _stop_run_for_local_proxy_pool_exhausted(ctx: ExecutionState) -> None:
    log_deduped_message(
        _PROXY_FETCH_FAILED_DEDUP_KEY,
        f"获取随机代理失败：{_LOCAL_PROXY_EXHAUSTED_MESSAGE}",
        level="WARNING",
    )
    ctx.mark_terminal_stop(
        "proxy_pool_exhausted",
        failure_reason="proxy_unavailable",
        message=_LOCAL_PROXY_EXHAUSTED_MESSAGE,
    )
    ctx.stop_event.set()


def _is_local_proxy_source(ctx: ExecutionState) -> bool:
    return str(ctx.config.proxy.source or "").strip().lower() == "local"


def _proxy_fetching_enabled(ctx: ExecutionState) -> bool:
    return bool(ctx.config.proxy.enabled) and not _is_local_proxy_source(ctx)


def _get_proxy_fetch_async_lock(ctx: ExecutionState) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    current_lock = getattr(ctx, "_proxy_fetch_async_lock", None)
    current_loop = getattr(ctx, "_proxy_fetch_async_lock_loop", None)
    if not isinstance(current_lock, asyncio.Lock) or current_loop is not loop:
        current_lock = asyncio.Lock()
        setattr(ctx, "_proxy_fetch_async_lock", current_lock)
        setattr(ctx, "_proxy_fetch_async_lock_loop", loop)
    return current_lock


def is_proxy_fetch_locked(ctx: ExecutionState) -> bool:
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
) -> bool:
    _ = ctx
    return False


def _resolve_proxy_request_num_locked(ctx: ExecutionState) -> int:
    waiting_count = max(1, int(ctx.proxy_waiting_threads or 0))
    active_count = len(ctx.proxy_in_use_by_thread)
    remaining_to_start = max(
        0, int(ctx.config.target_num or 0) - int(ctx.success_count or 0) - active_count
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
        reset_deduped_log_message(_PROXY_FETCH_FAILED_DEDUP_KEY)
        ctx.notify_runtime_change()
    return merged_count


def resolve_proxy_prefetch_request_count(ctx: ExecutionState) -> int:

    if not _proxy_fetching_enabled(ctx):
        return 0
    with ctx.lock:
        active_count = len(ctx.proxy_in_use_by_thread)
        remaining_to_start = max(
            0, int(ctx.config.target_num or 0) - int(ctx.success_count or 0) - active_count
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

    if not _proxy_fetching_enabled(ctx):
        return False
    if _should_stop_proxy_wait(ctx, ctx.stop_event):
        return False
    with ctx.lock:
        active_count = len(ctx.proxy_in_use_by_thread)
        remaining_to_start = max(
            0, int(ctx.config.target_num or 0) - int(ctx.success_count or 0) - active_count
        )
    return remaining_to_start > 0


def _should_stop_proxy_wait(
    ctx: ExecutionState,
    stop_signal: StopSignalLike | None,
) -> bool:
    if stop_signal is not None and stop_signal.is_set():
        return True
    return bool(ctx.stop_event and ctx.stop_event.is_set())


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
    if not ctx.config.proxy.enabled:
        return None
    selected: ProxyLease | None = None
    with ctx.lock:
        selected = _pop_available_proxy_lease_locked(ctx)
    if selected is not None:
        return _mark_proxy_in_use(ctx, thread_name, selected)

    if _is_local_proxy_source(ctx):
        if not wait:
            return None
        if ctx.config.proxy.reuse and ctx.proxy_in_use_by_thread:
            return None
        _stop_run_for_local_proxy_pool_exhausted(ctx)
        raise SubmitProxyUnavailableError(_LOCAL_PROXY_EXHAUSTED_MESSAGE)

    ctx.register_proxy_waiter()
    try:
        while True:
            if _should_stop_proxy_wait(ctx, stop_signal):
                return None
            with ctx.lock:
                selected = _pop_available_proxy_lease_locked(ctx)
            if selected is not None:
                return _mark_proxy_in_use(ctx, thread_name, selected)
            if _is_local_proxy_source(ctx):
                if not wait:
                    return None
                if ctx.config.proxy.reuse and ctx.proxy_in_use_by_thread:
                    return None
                _stop_run_for_local_proxy_pool_exhausted(ctx)
                raise SubmitProxyUnavailableError(_LOCAL_PROXY_EXHAUSTED_MESSAGE)
            if is_proxy_fetch_locked(ctx):
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
                    except ProxyApiNotConfiguredError as exc:
                        _stop_run_for_proxy_api_not_configured(ctx, exc)
                        raise SubmitProxyUnavailableError(str(exc)) from exc
                    except (RuntimeError, OSError) as exc:
                        log_deduped_message(
                            _PROXY_FETCH_FAILED_DEDUP_KEY,
                            f"获取随机代理失败：{exc}",
                            level="WARNING",
                        )
                        fetched = None
                    if fetched:
                        with ctx.lock:
                            selected = _merge_fetched_proxy_leases_locked(
                                ctx, fetched, select_first=True
                            )
                        if selected is not None:
                            reset_deduped_log_message(_PROXY_FETCH_FAILED_DEDUP_KEY)
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
        logger.opt(exception=True).debug("读取代理来源失败")
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
    released: object | None = None
    try:
        released = ctx.release_proxy_in_use(thread_name)
    except (KeyError, AttributeError):
        logger.opt(exception=True).debug("释放提交代理占用失败")
    if released is not None and ctx.config.proxy.reuse:
        try:
            _return_proxy_lease_to_pool(ctx, released)
        except Exception:
            logger.opt(exception=True).debug("复用模式下归还代理租约失败")


def mark_submit_proxy_success(ctx: ExecutionState, proxy_address: str | None) -> None:
    if not proxy_address:
        return
    try:
        ctx.mark_successful_proxy_address(proxy_address)
    except (AttributeError, KeyError):
        logger.opt(exception=True).info(f"记录成功代理失败：{proxy_address}")
