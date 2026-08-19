from __future__ import annotations

from pathlib import Path

def _get_repo_root() -> str:
    return str(Path(__file__).resolve().parent.parent.parent.parent)


def get_resource_path(relative_path: str) -> str:
    return str(Path(_get_repo_root()) / relative_path)


__all__ = [
    "get_resource_path",
]
