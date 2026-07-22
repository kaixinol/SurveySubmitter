from __future__ import annotations

from loguru import logger
from collections import deque
from dataclasses import dataclass
from typing import Iterable

from survey_submitter.core.task import ExecutionState, ProxyLease
from survey_submitter.network.proxy.pool import coerce_proxy_lease, mask_proxy_for_log
from survey_submitter.network.proxy.pool import (
    get_proxy_required_ttl_seconds,
    proxy_lease_has_sufficient_ttl,
)


_BAD_PROXY_COOLDOWN_SECONDS = 180.0


@dataclass(frozen=True)
class SubmitProxyLease:
    address: str | None
    provider: str = "unknown"


class SubmitProxyUnavailableError(RuntimeError):
    pass


def _ensure_proxy_pool_deque_locked(ctx: ExecutionState) -> deque:
    pool = ctx.config.proxy_ip_pool
    if isinstance(pool, deque):
        return pool
    normalized_pool = deque(pool or [])
    ctx.config.proxy_ip_pool = normalized_pool
    return normalized_pool


def _active_proxy_addresses_locked(
    ctx: ExecutionState, *, exclude_thread_name: str = ""
) -> set[str]:
    return ctx.active_proxy_addresses_locked(exclude_thread_name=exclude_thread_name)


def _excluded_proxy_addresses_locked(
    ctx: ExecutionState, *, exclude_thread_name: str = ""
) -> set[str]:
    blocked = _active_proxy_addresses_locked(ctx, exclude_thread_name=exclude_thread_name)
    blocked.update(ctx.successful_proxy_addresses_locked())
    return blocked


def _required_proxy_ttl_seconds(ctx: ExecutionState) -> int:
    return int(
        get_proxy_required_ttl_seconds(
            ctx.config.answer_duration_range_seconds,
            provider=ctx.config.provider,
        )
    )


def _mark_proxy_temporarily_bad(
    ctx: ExecutionState,
    proxy_address: str,
    *,
    cooldown_seconds: float = _BAD_PROXY_COOLDOWN_SECONDS,
) -> None:
    normalized = str(proxy_address or "").strip()
    if not normalized:
        return
    ctx.mark_proxy_in_cooldown(normalized, cooldown_seconds)
    _discard_unresponsive_proxy(ctx, normalized)
    logger.info(
        f"代理已本地临时屏蔽 {float(cooldown_seconds or 0.0):.0f}s：{mask_proxy_for_log(normalized)}"
    )


def _cooldown_proxy_addresses_locked(ctx: ExecutionState) -> set[str]:
    ctx._purge_expired_proxy_cooldowns_locked()
    return {
        str(address or "").strip()
        for address, cooldown_until in ctx.proxy_cooldowns_by_address.items()
        if str(address or "").strip() and float(cooldown_until or 0.0) > 0.0
    }


def _purge_unusable_proxy_pool_locked(
    ctx: ExecutionState,
    *,
    required_ttl: int | None = None,
    blocked_addresses: set[str] | None = None,
) -> set[str]:
    ctx._purge_expired_proxy_cooldowns_locked()
    required_ttl_seconds = (
        _required_proxy_ttl_seconds(ctx) if required_ttl is None else int(required_ttl)
    )
    pool = _ensure_proxy_pool_deque_locked(ctx)
    kept = deque()
    seen = set()
    removed = 0
    while pool:
        item = pool.popleft()
        lease = coerce_proxy_lease(item)
        if lease is None:
            removed += 1
            continue
        if not lease.poolable:
            removed += 1
            continue
        if lease.address in seen:
            removed += 1
            continue
        if ctx._is_proxy_in_cooldown_locked(lease.address):
            removed += 1
            logger.info(f"已移除本地临时屏蔽中的代理：{mask_proxy_for_log(lease.address)}")
            continue
        if not proxy_lease_has_sufficient_ttl(lease, required_ttl_seconds=required_ttl_seconds):
            removed += 1
            logger.info(f"已丢弃即将过期的代理：{mask_proxy_for_log(lease.address)}")
            continue
        seen.add(lease.address)
        kept.append(lease)
    if removed:
        logger.info(f"代理池已清理无效/重复代理 {removed} 个")
    ctx.config.proxy_ip_pool = kept
    if removed:
        ctx.notify_runtime_change()
    return seen


def _pop_available_proxy_lease_locked(ctx: ExecutionState) -> ProxyLease | None:
    required_ttl = _required_proxy_ttl_seconds(ctx)
    blocked_addresses = _excluded_proxy_addresses_locked(ctx)
    _purge_unusable_proxy_pool_locked(
        ctx,
        required_ttl=required_ttl,
        blocked_addresses=blocked_addresses,
    )
    pool = _ensure_proxy_pool_deque_locked(ctx)
    while pool:
        lease = coerce_proxy_lease(pool.popleft())
        if lease is None:
            continue
        if not proxy_lease_has_sufficient_ttl(lease, required_ttl_seconds=required_ttl):
            logger.info(f"已跳过即将过期的代理：{mask_proxy_for_log(lease.address)}")
            continue
        if ctx._is_proxy_in_cooldown_locked(lease.address):
            logger.info(f"已跳过本地临时屏蔽中的代理：{mask_proxy_for_log(lease.address)}")
            continue
        if lease.address in blocked_addresses:
            logger.info(f"已跳过已占用或已成功使用过的代理：{mask_proxy_for_log(lease.address)}")
            continue
        return lease
    return None


def _merge_fetched_proxy_leases_locked(
    ctx: ExecutionState,
    fetched: Iterable[object],
    *,
    select_first: bool,
) -> ProxyLease | None:
    required_ttl = _required_proxy_ttl_seconds(ctx)
    blocked_addresses = _excluded_proxy_addresses_locked(ctx)
    existing = _purge_unusable_proxy_pool_locked(
        ctx,
        required_ttl=required_ttl,
        blocked_addresses=blocked_addresses,
    )
    existing.update(blocked_addresses)
    pool = _ensure_proxy_pool_deque_locked(ctx)
    selected: ProxyLease | None = None
    changed = False

    for item in fetched or []:
        lease = coerce_proxy_lease(item)
        if lease is None:
            continue
        if not proxy_lease_has_sufficient_ttl(lease, required_ttl_seconds=required_ttl):
            logger.info(f"已丢弃即将过期的新代理：{mask_proxy_for_log(lease.address)}")
            continue
        if ctx._is_proxy_in_cooldown_locked(lease.address):
            logger.info(f"已跳过本地临时屏蔽中的新代理：{mask_proxy_for_log(lease.address)}")
            continue
        if lease.address in existing:
            logger.info(f"已跳过重复或正在占用的新代理：{mask_proxy_for_log(lease.address)}")
            continue
        if select_first and selected is None:
            selected = lease
            existing.add(lease.address)
            continue
        if not lease.poolable:
            continue
        pool.append(lease)
        existing.add(lease.address)
        changed = True

    if changed or selected is not None:
        ctx.notify_runtime_change()
    return selected


def _mark_proxy_in_use(
    ctx: ExecutionState, thread_name: str, lease: ProxyLease | None
) -> str | None:
    if lease is None:
        return None
    if thread_name:
        ctx.mark_proxy_in_use(thread_name, lease)
    logger.debug(
        f"线程[{thread_name or '?'}] 已分配随机IP：{mask_proxy_for_log(lease.address)}（来源={str(lease.source or 'unknown')}）"
    )
    return lease.address


def _discard_unresponsive_proxy(ctx: ExecutionState, proxy_address: str) -> None:
    if not proxy_address:
        return
    with ctx.lock:
        removed = False
        normalized = str(proxy_address or "").strip()
        retained = deque()
        pool = _ensure_proxy_pool_deque_locked(ctx)
        while pool:
            item = pool.popleft()
            lease = coerce_proxy_lease(item)
            if lease is None:
                continue
            if lease.address == normalized:
                removed = True
                continue
            retained.append(lease)
        ctx.config.proxy_ip_pool = retained
        if removed:
            logger.info(f"已移除无响应代理：{mask_proxy_for_log(proxy_address)}")
            ctx.notify_runtime_change()
