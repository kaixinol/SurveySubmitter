from __future__ import annotations

import logging
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


def _blocked_proxy_addresses_locked(
    ctx: ExecutionState, *, exclude_thread_name: str = ""
) -> set[str]:
    blocked = _active_proxy_addresses_locked(ctx, exclude_thread_name=exclude_thread_name)
    blocked.update(ctx.successful_proxy_addresses_locked())
    return blocked


def _required_proxy_ttl_seconds(ctx: ExecutionState) -> int:
    return int(
        get_proxy_required_ttl_seconds(
            ctx.config.answer_duration_range_seconds,
            survey_provider=ctx.config.survey_provider,
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
    logging.info(
        "\u4ee3\u7406\u5df2\u672c\u5730\u4e34\u65f6\u5c4f\u853d %.0fs\uff1a%s",
        float(cooldown_seconds or 0.0),
        mask_proxy_for_log(normalized),
    )


def _cooldown_proxy_addresses_locked(ctx: ExecutionState) -> set[str]:
    ctx._purge_expired_proxy_cooldowns_locked()
    return {
        str(address or "").strip()
        for address, cooldown_until in ctx.proxy_cooldown_until_by_address.items()
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
            logging.info(
                "\u5df2\u79fb\u9664\u672c\u5730\u4e34\u65f6\u5c4f\u853d\u4e2d\u7684\u4ee3\u7406\uff1a%s",
                mask_proxy_for_log(lease.address),
            )
            continue
        if not proxy_lease_has_sufficient_ttl(lease, required_ttl_seconds=required_ttl_seconds):
            removed += 1
            logging.info(
                "\u5df2\u4e22\u5f03\u5373\u5c06\u8fc7\u671f\u7684\u4ee3\u7406\uff1a%s",
                mask_proxy_for_log(lease.address),
            )
            continue
        seen.add(lease.address)
        kept.append(lease)
    if removed:
        logging.info(
            "\u4ee3\u7406\u6c60\u5df2\u6e05\u7406\u65e0\u6548/\u91cd\u590d\u4ee3\u7406 %s \u4e2a",
            removed,
        )
    ctx.config.proxy_ip_pool = kept
    if removed:
        ctx.notify_runtime_change()
    return seen


def _pop_available_proxy_lease_locked(ctx: ExecutionState) -> ProxyLease | None:
    required_ttl = _required_proxy_ttl_seconds(ctx)
    blocked_addresses = _blocked_proxy_addresses_locked(ctx)
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
            logging.info(
                "\u5df2\u8df3\u8fc7\u5373\u5c06\u8fc7\u671f\u7684\u4ee3\u7406\uff1a%s",
                mask_proxy_for_log(lease.address),
            )
            continue
        if ctx._is_proxy_in_cooldown_locked(lease.address):
            logging.info(
                "\u5df2\u8df3\u8fc7\u672c\u5730\u4e34\u65f6\u5c4f\u853d\u4e2d\u7684\u4ee3\u7406\uff1a%s",
                mask_proxy_for_log(lease.address),
            )
            continue
        if lease.address in blocked_addresses:
            logging.info(
                "\u5df2\u8df3\u8fc7\u5df2\u5360\u7528\u6216\u5df2\u6210\u529f\u4f7f\u7528\u8fc7\u7684\u4ee3\u7406\uff1a%s",
                mask_proxy_for_log(lease.address),
            )
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
    blocked_addresses = _blocked_proxy_addresses_locked(ctx)
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
            logging.info(
                "\u5df2\u4e22\u5f03\u5373\u5c06\u8fc7\u671f\u7684\u65b0\u4ee3\u7406\uff1a%s",
                mask_proxy_for_log(lease.address),
            )
            continue
        if ctx._is_proxy_in_cooldown_locked(lease.address):
            logging.info(
                "\u5df2\u8df3\u8fc7\u672c\u5730\u4e34\u65f6\u5c4f\u853d\u4e2d\u7684\u65b0\u4ee3\u7406\uff1a%s",
                mask_proxy_for_log(lease.address),
            )
            continue
        if lease.address in existing:
            logging.info(
                "\u5df2\u8df3\u8fc7\u91cd\u590d\u6216\u6b63\u5728\u5360\u7528\u7684\u65b0\u4ee3\u7406\uff1a%s",
                mask_proxy_for_log(lease.address),
            )
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
    logging.debug(
        "\u7ebf\u7a0b[%s] \u5df2\u5206\u914d\u968f\u673aIP\uff1a%s\uff08\u6765\u6e90=%s\uff09",
        thread_name or "?",
        mask_proxy_for_log(lease.address),
        str(lease.source or "unknown"),
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
            logging.info(
                f"\u5df2\u79fb\u9664\u65e0\u54cd\u5e94\u4ee3\u7406\uff1a{mask_proxy_for_log(proxy_address)}"
            )
            ctx.notify_runtime_change()
