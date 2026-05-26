"""Reachy Mini SDK."""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("reachy_mini")
except PackageNotFoundError:
    __version__ = "0+local"

__all__ = [
    "ActionExecutor",
    "ActionRegistry",
    "ActionSpec",
    "AgentConfig",
    "BrainAgent",
    "ReachyMini",
    "ReachyMiniApp",
    "RuntimeSession",
    "__version__",
]


def __getattr__(name: str) -> Any:
    """Load heavyweight exports lazily so submodule imports stay lightweight."""
    if name == "ReachyMini":
        from reachy_mini.reachy_mini import ReachyMini

        return ReachyMini
    if name == "ReachyMiniApp":
        from reachy_mini.apps.app import ReachyMiniApp

        return ReachyMiniApp
    if name == "RuntimeSession":
        from reachy_mini.pipeline.session import RuntimeSession

        return RuntimeSession
    if name == "BrainAgent":
        from reachy_mini.reachy_brain.agent import BrainAgent

        return BrainAgent
    if name == "AgentConfig":
        from reachy_mini.reachy_brain.config import AgentConfig

        return AgentConfig
    if name in {"ActionExecutor", "ActionRegistry", "ActionSpec"}:
        from reachy_mini.action_runtime import ActionExecutor, ActionRegistry, ActionSpec

        exports = {
            "ActionExecutor": ActionExecutor,
            "ActionRegistry": ActionRegistry,
            "ActionSpec": ActionSpec,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
