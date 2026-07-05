"""Reachy hardware adapter with dry-run safety default."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

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
from reachy_mini.utils.interpolation import InterpolationTechnique


@dataclass(slots=True)
class ReachyAdapter:
    """Hardware adapter that defaults to dry-run execution."""

    mini: Any | None = None
    context: AdapterContext = field(
        default_factory=lambda: AdapterContext(
            mode=RuntimeMode.HARDWARE,
            safety_profile="hardware_lite",
            options={"dry_run": True},
        )
    )
    adapter_id: str = "reachy"
    _lifecycle: LifecycleState = LifecycleState.UNCONFIGURED
    _active_commands: set[str] = field(default_factory=set)
    _last_heartbeat_ms: int | None = None

    async def configure(self, config: dict[str, object]) -> None:
        """Configure hardware adapter options."""
        options = {**self.context.options, **config}
        if "dry_run" not in options:
            options["dry_run"] = True
        self.context = AdapterContext(
            mode=RuntimeMode.HARDWARE,
            safety_profile=str(options.get("safety_profile", self.context.safety_profile)),
            options=options,
            publish_event=self.context.publish_event,
        )
        self._touch_heartbeat()
        self._lifecycle = LifecycleState.INACTIVE

    async def activate(self) -> None:
        """Activate hardware adapter state."""
        if self._lifecycle is LifecycleState.UNCONFIGURED:
            await self.configure({})
        self._touch_heartbeat()
        self._lifecycle = LifecycleState.ACTIVE

    async def deactivate(self) -> None:
        """Deactivate hardware adapter state."""
        self._active_commands.clear()
        self._lifecycle = LifecycleState.INACTIVE

    async def capabilities(self) -> list[Capability]:
        """Return conservative Reachy Mini capabilities."""
        return [
            Capability(
                capability_id="reachy:gaze:front",
                adapter_id=self.adapter_id,
                embodiment="reachy",
                modality="gaze",
                channels=["gaze", "head"],
                semantic_tags=["attention_shift", "look", "front"],
                input_schema={"command_type": "set_gaze"},
                constraints={"requires_safety": True},
            ),
            Capability(
                capability_id="reachy:motion:greet",
                adapter_id=self.adapter_id,
                embodiment="reachy",
                modality="motion",
                channels=["head", "gesture"],
                semantic_tags=["greet", "acknowledge"],
                input_schema={"command_type": "play_motion"},
                constraints={"requires_safety": True},
                timing_profile={"duration_ms": 1200},
            ),
        ]

    async def execute(self, command: RobotCommand) -> AdapterResult:
        """Execute a hardware command, or dry-run by default."""
        await self.activate()
        started_at_ms = now_ms()
        self._active_commands.add(command.command_id)
        try:
            self._touch_heartbeat()
            if command.command_type == "safe_idle":
                return _result(
                    command,
                    started_at_ms,
                    telemetry={
                        "dry_run": self._dry_run,
                        "safe_idle": True,
                        "source_capability": command.capability_id,
                    },
                )
            if self._dry_run:
                return _result(command, started_at_ms, telemetry={"dry_run": True})
            if self.mini is None:
                return _failure(command, "missing_mini", "Reachy mini instance is required")
            self._execute_on_mini(command)
            self._touch_heartbeat()
            return _result(command, started_at_ms, telemetry={"dry_run": False})
        finally:
            self._active_commands.discard(command.command_id)

    async def cancel(self, command_id: str, reason: str) -> AdapterResult:
        """Cancel a hardware command record."""
        self._active_commands.discard(command_id)
        return AdapterResult(
            command_id=command_id,
            adapter_id=self.adapter_id,
            status=AdapterResultStatus.CANCELLED,
            error_message=reason,
        )

    async def state(self) -> AdapterState:
        """Return hardware adapter state."""
        return AdapterState(
            adapter_id=self.adapter_id,
            lifecycle=self._lifecycle,
            mode=RuntimeMode.HARDWARE,
            active_command_ids=sorted(self._active_commands),
            metadata={
                "dry_run": self._dry_run,
                "last_heartbeat_ms": self._last_heartbeat_ms,
            },
        )

    @property
    def _dry_run(self) -> bool:
        return bool(self.context.options.get("dry_run", True))

    def _execute_on_mini(self, command: RobotCommand) -> None:
        call = _sdk_call_from_command(command)
        if call.method == "look_at_world":
            self.mini.look_at_world(*call.args, **call.kwargs)
            return
        if call.method == "set_target":
            self.mini.set_target(**call.kwargs)
            return
        if call.method == "goto_target":
            self.mini.goto_target(**call.kwargs)
            return
        if call.method == "play_move":
            self.mini.play_move(**call.kwargs)
            return
        raise ValueError(f"Unsupported Reachy SDK method: {call.method}")

    def _touch_heartbeat(self) -> None:
        self._last_heartbeat_ms = now_ms()


@dataclass(frozen=True, slots=True)
class _SdkCall:
    method: str
    args: tuple[object, ...] = ()
    kwargs: dict[str, object] = field(default_factory=dict)


def _sdk_call_from_command(command: RobotCommand) -> _SdkCall:
    payload = dict(command.payload)
    target = _target_payload(payload)
    duration_s = _duration_s(command, payload)
    if command.command_type == "set_gaze":
        point = _point_from_target(target)
        if point is not None:
            return _SdkCall(
                method="look_at_world",
                args=(point[0], point[1], point[2], duration_s, True),
            )
        return _SdkCall(
            method="goto_target",
            kwargs={**_movement_kwargs(command, payload), "duration": duration_s},
        )
    if command.command_type == "set_target":
        kwargs = _movement_kwargs(command, payload)
        if not kwargs:
            kwargs = _semantic_default_kwargs(command)
        return _SdkCall(method="set_target", kwargs=_without_goto_only_kwargs(kwargs))
    if command.command_type in {"play_motion", "goto_pose"}:
        kwargs = _movement_kwargs(command, payload)
        if not kwargs:
            kwargs = _semantic_default_kwargs(command)
        return _SdkCall(
            method="goto_target",
            kwargs={**kwargs, "duration": duration_s},
        )
    if command.command_type == "play_move":
        move = payload.get("move")
        if move is None:
            raise ValueError("play_move command requires payload.move")
        return _SdkCall(
            method="play_move",
            kwargs={
                "move": move,
                "initial_goto_duration": duration_s,
                "speed": float(payload.get("speed", 1.0)),
            },
        )
    raise ValueError(f"Unsupported Reachy command_type: {command.command_type}")


def _target_payload(payload: dict[str, object]) -> dict[str, object]:
    target = payload.get("target")
    return dict(target) if isinstance(target, dict) else {}


def _duration_s(command: RobotCommand, payload: dict[str, object]) -> float:
    for key in ("duration_s", "duration"):
        if payload.get(key) is not None:
            return max(0.001, float(payload[key]))
    if payload.get("duration_ms") is not None:
        return max(0.001, float(payload["duration_ms"]) / 1000.0)
    if command.duration_ms is not None:
        return max(0.001, command.duration_ms / 1000.0)
    return 0.5


def _point_from_target(target: dict[str, object]) -> tuple[float, float, float] | None:
    raw = target.get("point") or target.get("position")
    if raw is not None:
        values = [float(item) for item in raw]  # type: ignore[union-attr]
        if len(values) != 3:
            raise ValueError("target point must contain exactly 3 values")
        return (values[0], values[1], values[2])
    if all(key in target for key in ("x", "y", "z")):
        return (float(target["x"]), float(target["y"]), float(target["z"]))
    return None


def _movement_kwargs(
    command: RobotCommand,
    payload: dict[str, object],
) -> dict[str, object]:
    target = _target_payload(payload)
    kwargs: dict[str, object] = {}
    head = _head_pose(payload, target)
    antennas = _antennas(payload, target)
    body_yaw = _body_yaw(payload, target)
    method = _interpolation_method(payload)
    if head is not None:
        kwargs["head"] = head
    if antennas is not None:
        kwargs["antennas"] = antennas
    if body_yaw is not None:
        kwargs["body_yaw"] = body_yaw
    if method is not None:
        kwargs["method"] = method
    if not kwargs and command.command_type == "set_gaze":
        kwargs["head"] = _pose_from_euler()
    return kwargs


def _head_pose(
    payload: dict[str, object],
    target: dict[str, object],
) -> np.ndarray | None:
    raw = (
        payload.get("head_pose")
        or payload.get("pose")
        or target.get("head_pose")
        or target.get("pose")
    )
    if raw is not None:
        pose = np.array(raw, dtype=np.float64)
        if pose.shape != (4, 4):
            raise ValueError(f"head_pose must be 4x4, got {pose.shape}")
        return pose
    angles = _euler_angles(payload, target)
    if angles is None:
        return None
    return _pose_from_euler(
        roll_rad=angles[0],
        pitch_rad=angles[1],
        yaw_rad=angles[2],
    )


def _euler_angles(
    payload: dict[str, object],
    target: dict[str, object],
) -> tuple[float, float, float] | None:
    if any(key in target or key in payload for key in ("roll_rad", "pitch_rad", "yaw_rad")):
        return (
            float(payload.get("roll_rad", target.get("roll_rad", 0.0))),
            float(payload.get("pitch_rad", target.get("pitch_rad", 0.0))),
            float(payload.get("yaw_rad", target.get("yaw_rad", 0.0))),
        )
    if any(key in target or key in payload for key in ("roll_deg", "pitch_deg", "yaw_deg")):
        return (
            math.radians(float(payload.get("roll_deg", target.get("roll_deg", 0.0)))),
            math.radians(float(payload.get("pitch_deg", target.get("pitch_deg", 0.0)))),
            math.radians(float(payload.get("yaw_deg", target.get("yaw_deg", 0.0)))),
        )
    return None


def _pose_from_euler(
    *,
    roll_rad: float = 0.0,
    pitch_rad: float = 0.0,
    yaw_rad: float = 0.0,
) -> np.ndarray:
    from scipy.spatial.transform import Rotation as R

    pose = np.eye(4)
    pose[:3, :3] = R.from_euler(
        "xyz",
        [roll_rad, pitch_rad, yaw_rad],
        degrees=False,
    ).as_matrix()
    return pose


def _antennas(
    payload: dict[str, object],
    target: dict[str, object],
) -> list[float] | None:
    raw = payload.get("antennas") or target.get("antennas")
    if raw is not None:
        values = [float(item) for item in raw]  # type: ignore[union-attr]
        if len(values) != 2:
            raise ValueError("antennas must contain [right, left]")
        return values
    raw_deg = payload.get("antennas_deg") or target.get("antennas_deg")
    if raw_deg is not None:
        values = [math.radians(float(item)) for item in raw_deg]  # type: ignore[union-attr]
        if len(values) != 2:
            raise ValueError("antennas_deg must contain [right, left]")
        return values
    return None


def _body_yaw(
    payload: dict[str, object],
    target: dict[str, object],
) -> float | None:
    if payload.get("body_yaw") is not None or target.get("body_yaw") is not None:
        return float(payload.get("body_yaw", target.get("body_yaw")))
    if payload.get("body_yaw_deg") is not None or target.get("body_yaw_deg") is not None:
        return math.radians(float(payload.get("body_yaw_deg", target.get("body_yaw_deg"))))
    return None


def _interpolation_method(payload: dict[str, object]) -> InterpolationTechnique | None:
    value = payload.get("method") or payload.get("interpolation")
    if value is None:
        return None
    return InterpolationTechnique(str(value))


def _without_goto_only_kwargs(kwargs: dict[str, object]) -> dict[str, object]:
    result = dict(kwargs)
    result.pop("method", None)
    return result


def _semantic_default_kwargs(command: RobotCommand) -> dict[str, object]:
    tags = {str(tag) for tag in command.payload.get("semantic_tags", [])}
    intensity = float(command.payload.get("intensity", 0.5) or 0.5)
    if tags & {"greet", "acknowledge"}:
        amplitude = min(0.45, max(0.1, intensity * 0.5))
        return {
            "head": _pose_from_euler(yaw_rad=0.0, pitch_rad=math.radians(-4.0)),
            "antennas": [amplitude, -amplitude],
            "body_yaw": 0.0,
        }
    if tags & {"attention_shift", "look"}:
        return {"head": _pose_from_euler()}
    return {"body_yaw": 0.0}


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
        telemetry={**telemetry, "command_type": command.command_type},
    )


def _failure(command: RobotCommand, code: str, message: str) -> AdapterResult:
    return AdapterResult(
        command_id=command.command_id,
        adapter_id=command.adapter_id,
        status=AdapterResultStatus.FAILED,
        error_code=code,
        error_message=message,
    )


__all__ = ["ReachyAdapter"]
