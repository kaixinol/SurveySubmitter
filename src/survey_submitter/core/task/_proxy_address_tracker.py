from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from survey_submitter.core.task.proxy_state import ProxyLease


def get_active_proxy_addresses(
    proxy_in_use_by_thread: dict[str, ProxyLease],
    *,
    exclude_thread_name: str = "",
) -> set[str]:
    excluded = str(exclude_thread_name or "").strip()
    active = set()
    for thread_name, lease in proxy_in_use_by_thread.items():
        if excluded and str(thread_name or "").strip() == excluded:
            continue
        address = str(lease.address or "").strip()
        if address:
            active.add(address)
    return active


def get_successful_proxy_addresses(
    successful_proxy_addresses: set[str],
) -> set[str]:
    return {
        str(address or "").strip()
        for address in set(successful_proxy_addresses or set())
        if str(address or "").strip()
    }


def add_successful_proxy_address(
    successful_proxy_addresses: set[str],
    proxy_address: str,
) -> bool:
    normalized = str(proxy_address or "").strip()
    if not normalized:
        return False
    previous_size = len(successful_proxy_addresses)
    successful_proxy_addresses.add(normalized)
    return len(successful_proxy_addresses) != previous_size
