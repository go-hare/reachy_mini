"""RobotRuntime exception hierarchy."""

from __future__ import annotations


class RobotRuntimeError(RuntimeError):
    """Base error for RobotRuntime failures."""


class LifecycleTransitionError(RobotRuntimeError):
    """Raised when a lifecycle transition is not allowed."""


__all__ = ["LifecycleTransitionError", "RobotRuntimeError"]
