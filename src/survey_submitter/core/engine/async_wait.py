from __future__ import annotations

import asyncio
import logging
from typing import Protocol


class StopSignalProtocol(Protocol):
    """Protocol for objects that can signal stop requests."""

    def is_set(self) -> bool:
        """Check if stop is requested."""
        ...

    async def wait(self, timeout: float | None = None) -> bool:
        """Wait for stop signal or timeout."""
        ...

    _event: asyncio.Event


def _has_method(obj: StopSignalProtocol | asyncio.Event | None, name: str) -> bool:
    """Check if object has a callable method."""
    return obj is not None and hasattr(obj, name) and callable(getattr(obj, name))


def _get_method(obj: StopSignalProtocol | asyncio.Event | None, name: str):
    """Safely get a method from an object."""
    return getattr(obj, name, None) if _has_method(obj, name) else None


def is_stop_requested(stop_signal: StopSignalProtocol | asyncio.Event | None) -> bool:
    if stop_signal is None:
        return False

    is_set = _get_method(stop_signal, "is_set")
    if is_set is not None:
        try:
            return bool(is_set())
        except Exception:
            return False

    return False


def _resolve_async_event(
    stop_signal: StopSignalProtocol | asyncio.Event | None,
) -> asyncio.Event | None:
    if isinstance(stop_signal, asyncio.Event):
        return stop_signal

    event = getattr(stop_signal, "_event", None)
    if isinstance(event, asyncio.Event):
        return event

    return None


async def sleep_or_stop(
    stop_signal: StopSignalProtocol | asyncio.Event | None, seconds: float
) -> bool:
    delay = max(0.0, float(seconds or 0.0))
    if delay <= 0:
        return is_stop_requested(stop_signal)

    async_event = _resolve_async_event(stop_signal)
    if async_event is not None:
        if async_event.is_set():
            return True
        try:
            await asyncio.wait_for(async_event.wait(), timeout=delay)
            return True
        except asyncio.TimeoutError:
            return bool(async_event.is_set())

    if stop_signal is None:
        await asyncio.sleep(delay)
        return False

    waiter = _get_method(stop_signal, "wait")
    if waiter is not None:
        try:
            return bool(await asyncio.to_thread(waiter, delay))
        except Exception:
            logging.debug("stop_signal.wait() 调用失败", exc_info=True)

    await asyncio.sleep(delay)
    return is_stop_requested(stop_signal)


__all__ = ["is_stop_requested", "sleep_or_stop"]
