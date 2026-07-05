"""Optional runtime mirror publishers for bridge adapters."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from reachy_mini.robot_runtime.adapters.base import RobotAdapter
from reachy_mini.robot_runtime.contracts import RobotEvent, RobotState

LOGGER = logging.getLogger(__name__)


class RuntimeEventMirror(Protocol):
    """Optional adapter extension for mirroring RobotRuntime events."""

    async def publish_runtime_event(self, event: RobotEvent) -> dict[str, Any]:
        """Publish one runtime event to an external bridge."""


class RuntimeStateMirror(Protocol):
    """Optional adapter extension for mirroring RobotRuntime state."""

    async def publish_runtime_state(self, state: RobotState) -> dict[str, Any]:
        """Publish one runtime state snapshot to an external bridge."""


async def publish_event_mirrors(
    adapters: Mapping[str, RobotAdapter],
    event: RobotEvent,
) -> None:
    """Best-effort mirror of one event to adapters that support it."""
    for adapter in adapters.values():
        publisher = getattr(adapter, "publish_runtime_event", None)
        if publisher is None:
            continue
        try:
            await publisher(event)
        except Exception as exc:  # pragma: no cover - defensive bridge boundary
            LOGGER.warning("RobotRuntime event mirror failed for %s: %s", adapter.adapter_id, exc)


async def publish_state_mirrors(
    adapters: Mapping[str, RobotAdapter],
    state: RobotState,
) -> None:
    """Best-effort mirror of one state snapshot to adapters that support it."""
    for adapter in adapters.values():
        publisher = getattr(adapter, "publish_runtime_state", None)
        if publisher is None:
            continue
        try:
            await publisher(state)
        except Exception as exc:  # pragma: no cover - defensive bridge boundary
            LOGGER.warning("RobotRuntime state mirror failed for %s: %s", adapter.adapter_id, exc)


__all__ = [
    "RuntimeEventMirror",
    "RuntimeStateMirror",
    "publish_event_mirrors",
    "publish_state_mirrors",
]
