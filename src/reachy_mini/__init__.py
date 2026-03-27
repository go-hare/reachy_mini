"""Reachy Mini SDK."""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("reachy_mini")
except PackageNotFoundError:
    __version__ = "0+local"

__all__ = ["ReachyMini", "ReachyMiniApp", "__version__"]


def __getattr__(name: str) -> Any:
    """Load heavyweight exports lazily so submodule imports stay lightweight."""
    if name == "ReachyMini":
        from reachy_mini.reachy_mini import ReachyMini

        return ReachyMini
    if name == "ReachyMiniApp":
        from reachy_mini.apps.app import ReachyMiniApp

        return ReachyMiniApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
