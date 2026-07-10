from __future__ import annotations

import time


def purge_expired_proxy_cooldowns(
    cooldown_map: dict[str, float],
    *,
    now_ts: float | None = None,
) -> None:
    current = float(now_ts if now_ts is not None else time.time())
    expired = [
        address
        for address, cooldown_until in cooldown_map.items()
        if float(cooldown_until or 0.0) <= current
    ]
    for address in expired:
        cooldown_map.pop(address, None)


def is_proxy_in_cooldown(
    cooldown_map: dict[str, float],
    proxy_address: str,
    *,
    now_ts: float | None = None,
) -> bool:
    normalized = str(proxy_address or "").strip()
    if not normalized:
        return False
    purge_expired_proxy_cooldowns(cooldown_map, now_ts=now_ts)
    current = float(now_ts if now_ts is not None else time.time())
    return float(cooldown_map.get(normalized, 0.0) or 0.0) > current


def mark_proxy_in_cooldown(
    cooldown_map: dict[str, float],
    proxy_address: str,
    cooldown_seconds: float,
) -> bool:
    normalized = str(proxy_address or "").strip()
    if not normalized:
        return False
    try:
        seconds = max(0.0, float(cooldown_seconds))
    except (ValueError, TypeError):
        seconds = 0.0
    if seconds <= 0:
        return False
    cooldown_until = time.time() + seconds
    previous_until = float(cooldown_map.get(normalized, 0.0) or 0.0)
    cooldown_map[normalized] = max(previous_until, cooldown_until)
    return True
