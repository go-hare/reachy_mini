"""Public action contracts for the v4 action runtime."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import ActionCancelledError


def make_action_id(prefix: str = "action") -> str:
    """Create a compact trace id for action runtime objects."""
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class ActionSpec:
    """Serializable action intent produced by Brain or a worker."""

    name: str
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    request_id: str = ""
    owner_id: str = ""
    priority: int = 40
    interruptible: bool = True
    deadline_s: float | None = None
    parent_request_id: str | None = None


class CancelToken:
    """Cooperative cancellation token shared by executor, locks, and actions."""

    def __init__(self) -> None:
        """Initialize an unset cancellation token."""
        self._event = asyncio.Event()
        self.cancelled_at: float | None = None

    @property
    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()

    def cancel(self) -> None:
        """Request cancellation idempotently."""
        if self._event.is_set():
            return
        self.cancelled_at = time.monotonic()
        self._event.set()

    async def wait(self) -> None:
        """Wait until cancellation is requested."""
        await self._event.wait()

    async def checkpoint(self) -> None:
        """Raise if cancellation has been requested."""
        if self._event.is_set():
            raise ActionCancelledError("Action was cancelled.")
        await asyncio.sleep(0)


class Clock(Protocol):
    """Minimal clock protocol for tests and runtime code."""

    def monotonic(self) -> float:
        """Return monotonic seconds."""
        ...


class SystemClock:
    """Clock implementation backed by ``time.monotonic``."""

    def monotonic(self) -> float:
        """Return monotonic seconds."""
        return time.monotonic()


class ActionContext(Protocol):
    """Runtime context passed to a concrete robot action."""

    mini: Any
    cancel_token: CancelToken
    logger: Any
    request_id: str
    owner_id: str
    lease_handles: dict[str, Any]
    clock: Clock


@dataclass
class RobotAction:
    """SDK-side executable robot action."""

    name: str
    required_locks: set[str]
    duration_s: float | None
    priority: int
    interruptible: bool

    async def prepare(self, context: ActionContext) -> None:
        """Validate runtime state before issuing robot commands."""

    async def run(self, context: ActionContext) -> Any:
        """Execute the action."""

    async def cancel(self, context: ActionContext) -> None:
        """Handle cooperative cancellation."""

    async def cleanup(self, context: ActionContext) -> None:
        """Release transient action-local state."""


@dataclass
class ExecutorActionContext:
    """Concrete action context created by ``ActionExecutor``."""

    mini: Any
    cancel_token: CancelToken
    logger: Any
    request_id: str
    owner_id: str
    lease_handles: dict[str, Any]
    clock: Clock


@dataclass(frozen=True)
class ActionResult:
    """Result emitted after an action finishes, fails, or is cancelled."""

    request_id: str
    action_id: str
    name: str
    owner_id: str
    status: str
    result: Any = None
    error: str | None = None
    duration_ms: int = 0
    reason: str = ""
