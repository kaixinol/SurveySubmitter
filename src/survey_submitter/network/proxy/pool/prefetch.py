from __future__ import annotations

import asyncio
import threading

from survey_submitter.core.task import ProxyLease
from survey_submitter.network.proxy.policy.source import get_effective_proxy_api_url


def prefetch_proxy_pool(
    expected_count: int,
    proxy_api_url: str | None = None,
    stop_signal: threading.Event | None = None,
) -> list[ProxyLease]:

    from survey_submitter.network.proxy.api import fetch_proxy_batch_async

    effective_url = proxy_api_url or get_effective_proxy_api_url()
    proxy_pool = asyncio.run(
        fetch_proxy_batch_async(
            expected_count=max(1, expected_count),
            proxy_url=effective_url,
            notify_on_area_error=False,
            stop_signal=stop_signal,
        )
    )
    return proxy_pool
