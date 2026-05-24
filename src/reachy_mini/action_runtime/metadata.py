"""Action metadata used by registry, tools, CLI, and UI adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .action import ActionSpec, RobotAction

ActionBuilder = Callable[[ActionSpec], RobotAction]


@dataclass(frozen=True)
class ActionMetadata:
    """Describes one action without exposing SDK implementation details."""

    name: str
    description: str
    parameter_schema: dict[str, Any]
    required_locks: frozenset[str]
    default_priority: int
    default_interruptible: bool
    default_duration_s: float | None
    tags: list[str] = field(default_factory=list)
    safety_notes: str = ""
