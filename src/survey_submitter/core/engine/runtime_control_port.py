from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from survey_submitter.core.engine.stop_signal import StopSignalLike


class RuntimeControlPort(Protocol):
    def wait_if_paused(self, stop_signal: StopSignalLike | None) -> None: ...

    def on_random_ip_submission(self, stop_signal: StopSignalLike | None = None) -> None: ...

    def on_random_ip_loading_changed(self, loading: bool, message: str = "") -> None: ...


def _get_method_by_priority(
    obj: RuntimeControlPort, names: list[str]
) -> Callable[..., object] | None:
    """Get the first callable method from a list of method names."""
    for name in names:
        method = getattr(obj, name, None)
        if method is not None and callable(method):
            return method  # type: ignore[return-value]
    return None


def wait_if_paused(
    runtime_port: RuntimeControlPort | None, stop_signal: StopSignalLike | None
) -> None:
    if runtime_port is None:
        return
    runtime_port.wait_if_paused(stop_signal)


def on_random_ip_submission(
    runtime_port: RuntimeControlPort | None,
    stop_signal: StopSignalLike | None,
) -> None:
    if runtime_port is None:
        return

    handler = _get_method_by_priority(
        runtime_port, ["on_random_ip_submission", "handle_random_ip_submission"]
    )
    if handler is None:
        return

    try:
        handler(stop_signal)
    except TypeError:
        # Fallback for legacy handlers that require keyword argument
        if hasattr(handler, "__name__") and "legacy" in handler.__name__.lower():
            handler(stop_signal=stop_signal)
        else:
            raise


def on_random_ip_loading_changed(
    runtime_port: RuntimeControlPort | None,
    loading: bool,
    message: str = "",
) -> None:
    if runtime_port is None:
        return

    setter = _get_method_by_priority(
        runtime_port, ["on_random_ip_loading_changed", "set_random_ip_loading"]
    )
    if setter is not None:
        setter(bool(loading), str(message or ""))


__all__ = [
    "RuntimeControlPort",
    "on_random_ip_loading_changed",
    "on_random_ip_submission",
    "wait_if_paused",
]
