"""Base adapter protocol for RobotRuntime bodies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from reachy_mini.robot_runtime.contracts import (
    AdapterResult,
    AdapterState,
    Capability,
    RobotCommand,
    RobotEvent,
    RuntimeMode,
)

PublishRobotEvent = Callable[[RobotEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AdapterContext:
    """Runtime context injected into concrete adapters."""

    mode: RuntimeMode = RuntimeMode.AVATAR_ONLY
    safety_profile: str = "avatar"
    options: dict[str, object] = field(default_factory=dict)
    publish_event: PublishRobotEvent | None = None


class RobotAdapter(Protocol):
    """Protocol every RobotRuntime adapter must implement."""

    adapter_id: str

    async def configure(self, config: dict[str, object]) -> None:
        """Configure adapter resources."""

    async def activate(self) -> None:
        """Activate the adapter."""

    async def deactivate(self) -> None:
        """Deactivate the adapter."""

    async def capabilities(self) -> list[Capability]:
        """Return body capabilities."""

    async def execute(self, command: RobotCommand) -> AdapterResult:
        """Execute one safety-approved command."""

    async def cancel(self, command_id: str, reason: str) -> AdapterResult:
        """Cancel one command."""

    async def state(self) -> AdapterState:
        """Return adapter state."""


__all__ = ["AdapterContext", "PublishRobotEvent", "RobotAdapter"]
