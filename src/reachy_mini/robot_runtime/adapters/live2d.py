"""Live2D RobotRuntime adapter."""

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
from reachy_mini.runtime.live2d_avatar import Live2DCapabilities, Live2DNativeAction

PublishFrame = Callable[[EmbodimentFrame], Awaitable[None]]


@dataclass(slots=True)
class Live2DAdapter:
    """Adapter that maps RobotCommand objects to Live2D embodiment frames."""

    capabilities_model: Live2DCapabilities
    context: AdapterContext = field(default_factory=AdapterContext)
    publish_frame: PublishFrame | None = None
    adapter_id: str = "live2d"
    _lifecycle: LifecycleState = LifecycleState.UNCONFIGURED
    _active_commands: set[str] = field(default_factory=set)

    async def configure(self, config: dict[str, object]) -> None:
        """Configure adapter options."""
        options = {**self.context.options, **config}
        self.context = AdapterContext(
            mode=RuntimeMode.AVATAR_ONLY,
            safety_profile=str(options.get("safety_profile", self.context.safety_profile)),
            options=options,
            publish_event=self.context.publish_event,
        )
        self._lifecycle = LifecycleState.INACTIVE

    async def activate(self) -> None:
        """Activate Live2D output."""
        if self._lifecycle is LifecycleState.UNCONFIGURED:
            await self.configure({})
        self._lifecycle = LifecycleState.ACTIVE

    async def deactivate(self) -> None:
        """Deactivate Live2D output."""
        self._active_commands.clear()
        self._lifecycle = LifecycleState.INACTIVE

    async def capabilities(self) -> list[Capability]:
        """Return Live2D capabilities as semantic adapter inventory."""
        items: list[Capability] = []
        for detail in _details(
            self.capabilities_model.motion_details,
            self.capabilities_model.motions,
            kind="motion",
        ):
            items.append(_capability(self.adapter_id, detail, kind="motion"))
        for detail in _details(
            self.capabilities_model.expression_details,
            self.capabilities_model.expressions,
            kind="expression",
        ):
            items.append(_capability(self.adapter_id, detail, kind="expression"))
        return items

    async def execute(self, command: RobotCommand) -> AdapterResult:
        """Publish one Live2D embodiment frame and return an adapter result."""
        started_at_ms = now_ms()
        await self.activate()
        self._active_commands.add(command.command_id)
        try:
            if command.command_type == "safe_idle":
                return _result(
                    command,
                    AdapterResultStatus.COMPLETED,
                    started_at_ms=started_at_ms,
                    telemetry={
                        "safe_idle": True,
                        "source_capability": command.capability_id,
                    },
                )
            action, payload = await self._frame_payload(command)
            frame = EmbodimentFrame(
                action=action,
                target="live2d",
                turn_id=str(command.payload.get("turn_id", "")),
                payload=payload,
                ts_ms=started_at_ms,
            )
            if self.publish_frame is not None:
                await self.publish_frame(frame)
            return _result(
                command,
                AdapterResultStatus.COMPLETED,
                started_at_ms=started_at_ms,
                telemetry={"frame_action": action},
            )
        finally:
            self._active_commands.discard(command.command_id)

    async def cancel(self, command_id: str, reason: str) -> AdapterResult:
        """Cancel a Live2D command."""
        self._active_commands.discard(command_id)
        return AdapterResult(
            command_id=command_id,
            adapter_id=self.adapter_id,
            status=AdapterResultStatus.CANCELLED,
            error_message=reason,
        )

    async def state(self) -> AdapterState:
        """Return adapter state."""
        return AdapterState(
            adapter_id=self.adapter_id,
            lifecycle=self._lifecycle,
            mode=RuntimeMode.AVATAR_ONLY,
            active_command_ids=sorted(self._active_commands),
            metadata={"options": dict(self.context.options)},
        )

    async def _frame_payload(self, command: RobotCommand) -> tuple[str, dict[str, object]]:
        capabilities = {item.capability_id: item for item in await self.capabilities()}
        capability = capabilities.get(command.capability_id)
        if capability is None:
            raise ValueError(f"Unknown Live2D capability: {command.capability_id}")
        source = capability.source_asset or {}
        native_name = str(command.payload.get("name") or source.get("native_name") or "")
        if not native_name:
            raise ValueError(f"Live2D capability has no native name: {command.capability_id}")
        action = "live2d_expression" if capability.modality == "expression" else "live2d_motion"
        return action, {"name": native_name}


def _details(
    details: tuple[Live2DNativeAction, ...],
    names: tuple[str, ...],
    *,
    kind: str,
) -> tuple[Live2DNativeAction, ...]:
    if details:
        return details
    suffix = ".motion3.json" if kind == "motion" else ".exp3.json"
    return tuple(Live2DNativeAction(name=name, file_name=f"{name}{suffix}") for name in names)


def _capability(adapter_id: str, detail: Live2DNativeAction, *, kind: str) -> Capability:
    modality = "expression" if kind == "expression" else "motion"
    channels = ["face"] if modality == "expression" else ["face", "gesture"]
    return Capability(
        capability_id=f"{adapter_id}:{modality}:{detail.name}",
        adapter_id=adapter_id,
        embodiment="live2d",
        modality=modality,
        channels=channels,
        semantic_tags=_semantic_tags(detail, modality),
        affect_range=list(detail.aliases),
        timing_profile={"duration_ms": int((detail.duration_s or 1.0) * 1000)},
        interruptibility="soft",
        source_asset={"native_name": detail.name},
        confidence=0.8,
    )


def _semantic_tags(detail: Live2DNativeAction, modality: str) -> list[str]:
    tags = [modality, detail.name, *detail.aliases, *detail.parameter_labels]
    lowered = {str(item).strip().lower() for item in tags if str(item).strip()}
    if lowered & {"wave", "挥手", "招手", "hello", "hi"}:
        tags.append("greet")
    return sorted({str(item).strip() for item in tags if str(item).strip()})


def _result(
    command: RobotCommand,
    status: AdapterResultStatus,
    *,
    started_at_ms: int,
    telemetry: dict[str, object],
) -> AdapterResult:
    ended_at_ms = now_ms()
    return AdapterResult(
        command_id=command.command_id,
        adapter_id=command.adapter_id,
        status=status,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        duration_ms=max(0, ended_at_ms - started_at_ms),
        telemetry=telemetry,
    )


__all__ = ["Live2DAdapter", "PublishFrame"]
