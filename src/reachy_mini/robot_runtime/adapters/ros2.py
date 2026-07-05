"""ROS2 adapter facade for RobotRuntime commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from reachy_mini.robot_runtime.adapters.base import AdapterContext
from reachy_mini.robot_runtime.bridges.ros2_bridge import ROS2Bridge
from reachy_mini.robot_runtime.contracts import (
    AdapterResult,
    AdapterResultStatus,
    AdapterState,
    Capability,
    LifecycleState,
    RobotCommand,
    RobotEvent,
    RobotState,
    RuntimeMode,
    now_ms,
)

PublishMessage = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class ROS2Adapter:
    """Adapter that serializes approved Runtime commands for a ROS2 bridge."""

    bridge: ROS2Bridge = field(default_factory=ROS2Bridge)
    context: AdapterContext = field(default_factory=lambda: AdapterContext(mode=RuntimeMode.HYBRID))
    publish_message: PublishMessage | None = None
    adapter_id: str = "ros2"
    _lifecycle: LifecycleState = LifecycleState.UNCONFIGURED
    _active_commands: set[str] = field(default_factory=set)

    async def configure(self, config: dict[str, object]) -> None:
        """Configure ROS2 bridge options."""
        options = {**self.context.options, **config}
        namespace = str(options.get("namespace") or self.bridge.namespace)
        self.bridge = ROS2Bridge(namespace=namespace)
        self.context = AdapterContext(
            mode=RuntimeMode.HYBRID,
            safety_profile=str(options.get("safety_profile", self.context.safety_profile)),
            options=options,
            publish_event=self.context.publish_event,
        )
        self._lifecycle = LifecycleState.INACTIVE

    async def activate(self) -> None:
        """Activate ROS2 command publication."""
        if self._lifecycle is LifecycleState.UNCONFIGURED:
            await self.configure({})
        self._lifecycle = LifecycleState.ACTIVE

    async def deactivate(self) -> None:
        """Deactivate ROS2 command publication."""
        self._active_commands.clear()
        self._lifecycle = LifecycleState.INACTIVE

    async def capabilities(self) -> list[Capability]:
        """Return ROS2 bridge capabilities."""
        return [
            Capability(
                capability_id="ros2:motion:greet",
                adapter_id=self.adapter_id,
                embodiment="ros2",
                modality="motion",
                channels=["gesture", "head"],
                semantic_tags=["greet", "acknowledge"],
                input_schema={"command_type": "play_motion"},
                constraints={"requires_safety": True},
                confidence=0.65,
            ),
            Capability(
                capability_id="ros2:gaze:attention",
                adapter_id=self.adapter_id,
                embodiment="ros2",
                modality="gaze",
                channels=["gaze", "head"],
                semantic_tags=["attention_shift", "look"],
                input_schema={"command_type": "set_gaze"},
                constraints={"requires_safety": True},
                confidence=0.65,
            ),
            Capability(
                capability_id="ros2:task:execute",
                adapter_id=self.adapter_id,
                embodiment="ros2",
                modality="navigation",
                channels=["navigation"],
                semantic_tags=["task_execute"],
                input_schema={"command_type": "execute_task"},
                constraints={"requires_safety": True},
                confidence=0.6,
            ),
        ]

    async def execute(self, command: RobotCommand) -> AdapterResult:
        """Publish one approved command to a ROS2-compatible message sink."""
        await self.activate()
        started_at_ms = now_ms()
        self._active_commands.add(command.command_id)
        try:
            message = self.bridge.command_message(command)
            if self.publish_message is not None:
                await self.publish_message(message)
            return _result(
                command,
                started_at_ms,
                telemetry={
                    "message": message,
                    "safe_idle": command.command_type == "safe_idle",
                },
            )
        finally:
            self._active_commands.discard(command.command_id)

    async def publish_runtime_state(self, state: RobotState) -> dict[str, Any]:
        """Publish one runtime state snapshot to a ROS2-compatible message sink."""
        message = self.bridge.state_message(state)
        if self.publish_message is not None:
            await self.publish_message(message)
        return message

    async def publish_runtime_event(self, event: RobotEvent) -> dict[str, Any]:
        """Publish one runtime event to a ROS2-compatible message sink."""
        message = self.bridge.event_message(event)
        if self.publish_message is not None:
            await self.publish_message(message)
        return message

    async def cancel(self, command_id: str, reason: str) -> AdapterResult:
        """Cancel a ROS2 adapter command record."""
        self._active_commands.discard(command_id)
        return AdapterResult(
            command_id=command_id,
            adapter_id=self.adapter_id,
            status=AdapterResultStatus.CANCELLED,
            error_message=reason,
        )

    async def state(self) -> AdapterState:
        """Return ROS2 adapter state."""
        return AdapterState(
            adapter_id=self.adapter_id,
            lifecycle=self._lifecycle,
            mode=RuntimeMode.HYBRID,
            active_command_ids=sorted(self._active_commands),
            metadata={
                "namespace": self.bridge.namespace,
                "options": dict(self.context.options),
            },
        )


def _result(
    command: RobotCommand,
    started_at_ms: int,
    *,
    telemetry: dict[str, object],
) -> AdapterResult:
    ended_at_ms = now_ms()
    return AdapterResult(
        command_id=command.command_id,
        adapter_id=command.adapter_id,
        status=AdapterResultStatus.COMPLETED,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        duration_ms=max(0, ended_at_ms - started_at_ms),
        telemetry=telemetry,
    )


__all__ = ["PublishMessage", "ROS2Adapter"]
