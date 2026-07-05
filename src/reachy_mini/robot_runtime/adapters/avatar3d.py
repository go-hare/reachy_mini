"""3D avatar RobotRuntime adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from reachy_mini.embodiment import EmbodimentFrame
from reachy_mini.robot_runtime.adapters.base import AdapterContext
from reachy_mini.robot_runtime.contracts import (
    AdapterResult,
    AdapterResultStatus,
    AdapterState,
    Capability,
    LifecycleState,
    RobotCommand,
    RuntimeMode,
    now_ms,
)

PublishFrame = Callable[[EmbodimentFrame], Awaitable[None]]


@dataclass(slots=True)
class Avatar3DAdapter:
    """Adapter that maps Runtime commands to body-agnostic 3D avatar frames."""

    context: AdapterContext = field(default_factory=AdapterContext)
    publish_frame: PublishFrame | None = None
    adapter_id: str = "avatar3d"
    _lifecycle: LifecycleState = LifecycleState.UNCONFIGURED
    _active_commands: set[str] = field(default_factory=set)

    async def configure(self, config: dict[str, object]) -> None:
        """Configure 3D avatar adapter options."""
        options = {**self.context.options, **config}
        self.context = AdapterContext(
            mode=RuntimeMode.AVATAR_ONLY,
            safety_profile=str(options.get("safety_profile", self.context.safety_profile)),
            options=options,
            publish_event=self.context.publish_event,
        )
        self._lifecycle = LifecycleState.INACTIVE

    async def activate(self) -> None:
        """Activate 3D avatar output."""
        if self._lifecycle is LifecycleState.UNCONFIGURED:
            await self.configure({})
        self._lifecycle = LifecycleState.ACTIVE

    async def deactivate(self) -> None:
        """Deactivate 3D avatar output."""
        self._active_commands.clear()
        self._lifecycle = LifecycleState.INACTIVE

    async def capabilities(self) -> list[Capability]:
        """Return 3D avatar semantic capabilities."""
        return [
            Capability(
                capability_id="avatar3d:animation:greet",
                adapter_id=self.adapter_id,
                embodiment="avatar3d",
                modality="motion",
                channels=["gesture"],
                semantic_tags=["greet", "acknowledge", "wave"],
                input_schema={"command_type": "play_animation"},
                timing_profile={"duration_ms": 1000},
                confidence=0.72,
            ),
            Capability(
                capability_id="avatar3d:blendshape:speech",
                adapter_id=self.adapter_id,
                embodiment="avatar3d",
                modality="expression",
                channels=["face"],
                semantic_tags=["listen", "think", "speak"],
                input_schema={"command_type": "set_blendshape"},
                confidence=0.7,
            ),
            Capability(
                capability_id="avatar3d:gaze:attention",
                adapter_id=self.adapter_id,
                embodiment="avatar3d",
                modality="gaze",
                channels=["gaze", "head"],
                semantic_tags=["attention_shift", "look"],
                input_schema={"command_type": "set_gaze"},
                confidence=0.68,
            ),
        ]

    async def execute(self, command: RobotCommand) -> AdapterResult:
        """Publish one 3D avatar embodiment frame."""
        await self.activate()
        started_at_ms = now_ms()
        self._active_commands.add(command.command_id)
        try:
            if command.command_type == "safe_idle":
                frame = EmbodimentFrame(
                    action="avatar3d_safe_idle",
                    target="avatar3d",
                    turn_id=str(command.payload.get("turn_id", "")),
                    payload={
                        "command_type": command.command_type,
                        "capability_id": command.capability_id,
                        "safe_idle": True,
                    },
                    ts_ms=started_at_ms,
                )
                if self.publish_frame is not None:
                    await self.publish_frame(frame)
                return _result(
                    command,
                    started_at_ms,
                    telemetry={"frame_action": frame.action, "safe_idle": True},
                )
            frame = EmbodimentFrame(
                action=_frame_action(command.command_type),
                target="avatar3d",
                turn_id=str(command.payload.get("turn_id", "")),
                payload={
                    "command_type": command.command_type,
                    "capability_id": command.capability_id,
                    **dict(command.payload),
                },
                ts_ms=started_at_ms,
            )
            if self.publish_frame is not None:
                await self.publish_frame(frame)
            return _result(
                command,
                started_at_ms,
                telemetry={"frame_action": frame.action},
            )
        finally:
            self._active_commands.discard(command.command_id)

    async def cancel(self, command_id: str, reason: str) -> AdapterResult:
        """Cancel an avatar command record."""
        self._active_commands.discard(command_id)
        return AdapterResult(
            command_id=command_id,
            adapter_id=self.adapter_id,
            status=AdapterResultStatus.CANCELLED,
            error_message=reason,
        )

    async def state(self) -> AdapterState:
        """Return 3D avatar adapter state."""
        return AdapterState(
            adapter_id=self.adapter_id,
            lifecycle=self._lifecycle,
            mode=RuntimeMode.AVATAR_ONLY,
            active_command_ids=sorted(self._active_commands),
            metadata={"options": dict(self.context.options)},
        )


def _frame_action(command_type: str) -> str:
    if command_type == "set_blendshape":
        return "avatar3d_blendshape"
    if command_type == "set_gaze":
        return "avatar3d_gaze"
    return "avatar3d_animation"


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


__all__ = ["Avatar3DAdapter", "PublishFrame"]
