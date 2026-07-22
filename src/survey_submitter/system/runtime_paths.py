from __future__ import annotations

import sys
from pathlib import Path


def _get_repo_root() -> str:
    return str(Path(__file__).resolve().parent.parent.parent.parent)


def _is_frozen() -> bool:
    """Check if the application is running as a frozen (compiled) executable."""
    return bool(getattr(sys, "frozen", False))


def _get_bundle_extraction_dir() -> str | None:
    """Get the PyInstaller temporary extraction directory path."""
    return getattr(sys, "_MEIPASS", None)


def get_runtime_directory() -> str:

    if _is_frozen():
        exe_dir = str(Path(sys.executable).parent)
        if Path(exe_dir).name.lower() == "lib":
            return str(Path(exe_dir).parent)
        return exe_dir
    return _get_repo_root()


def get_bundle_resource_root() -> str:

    if _is_frozen():
        meipass = _get_bundle_extraction_dir()
        if meipass:
            return str(Path(meipass))
        return str(Path(sys.executable).parent)
    return _get_repo_root()


def get_assets_directory() -> str:

    bundle_root = get_bundle_resource_root()
    candidates = [str(Path(bundle_root) / "assets")]

    if _is_frozen():
        exe_dir = str(Path(sys.executable).parent)
        exe_assets = str(Path(exe_dir) / "assets")
        internal_assets = str(Path(exe_dir) / "_internal" / "assets")
        for path in (exe_assets, internal_assets):
            if path not in candidates:
                candidates.append(path)

    for path in candidates:
        if Path(path).is_dir():
            return path

    return str(Path(bundle_root) / "assets")


def get_resource_path(relative_path: str) -> str:

    return str(Path(get_bundle_resource_root()) / relative_path)


__all__ = [
    "get_runtime_directory",
    "get_bundle_resource_root",
    "get_assets_directory",
    "get_resource_path",
]
