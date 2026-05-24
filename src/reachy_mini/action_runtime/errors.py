"""Exceptions raised by the v4 action runtime."""

from __future__ import annotations


class ActionRuntimeError(Exception):
    """Base class for v4 action runtime failures."""


class UnknownActionError(ActionRuntimeError):
    """Raised when an action name is not registered."""


class DuplicateActionError(ActionRuntimeError):
    """Raised when an action name is registered more than once."""


class ActionParamError(ActionRuntimeError):
    """Raised when an action spec does not match its parameter schema."""


class LockBusyError(ActionRuntimeError):
    """Raised when a lock is held by another action and cannot be preempted."""


class LockTimeoutError(ActionRuntimeError):
    """Raised when waiting for a lock exceeds the allowed timeout."""


class ActionRunError(ActionRuntimeError):
    """Raised when a robot action fails while running."""


class ActionCancelledError(ActionRuntimeError):
    """Raised when an action is cancelled by preemption, timeout, or stop."""
