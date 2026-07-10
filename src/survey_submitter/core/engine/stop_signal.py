from __future__ import annotations

from typing import Protocol


class StopSignalLike(Protocol):
    def is_set(self) -> bool: ...

    def set(self) -> None: ...

    def wait(self, timeout: float | None = None) -> bool: ...


__all__ = ["StopSignalLike"]
