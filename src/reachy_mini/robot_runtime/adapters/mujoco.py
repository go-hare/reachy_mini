"""MuJoCo simulation adapter for RobotRuntime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
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

ExecuteCommand = Callable[[RobotCommand], Awaitable[dict[str, object]]]


@dataclass(slots=True)
class MujocoAdapter:
    """Simulation adapter with injectable command execution."""

    context: AdapterContext = field(
        default_factory=lambda: AdapterContext(mode=RuntimeMode.SIMULATION)
    )
    execute_command: ExecuteCommand | None = None
    backend: Any | None = None
    adapter_id: str = "mujoco"
    _lifecycle: LifecycleState = LifecycleState.UNCONFIGURED
    _active_commands: set[str] = field(default_factory=set)

    async def configure(self, config: dict[str, object]) -> None:
        """Configure simulation options."""
        options = {**self.context.options, **config}
        self.context = AdapterContext(
            mode=RuntimeMode.SIMULATION,
            safety_profile=str(options.get("safety_profile", self.context.safety_profile)),
            options=options,
            publish_event=self.context.publish_event,
        )
        self._lifecycle = LifecycleState.INACTIVE

    async def activate(self) -> None:
        """Activate simulation command execution."""
        if self._lifecycle is LifecycleState.UNCONFIGURED:
            await self.configure({})
        self._lifecycle = LifecycleState.ACTIVE

    async def deactivate(self) -> None:
        """Deactivate simulation command execution."""
        self._active_commands.clear()
        self._lifecycle = LifecycleState.INACTIVE

    async def capabilities(self) -> list[Capability]:
        """Return default Reachy-shaped simulation capabilities."""
        return [
            Capability(
                capability_id="mujoco:gaze:front",
                adapter_id=self.adapter_id,
                embodiment="mujoco",
                modality="gaze",
                channels=["gaze", "head"],
                semantic_tags=["attention_shift", "look", "front"],
                input_schema={"command_type": "set_gaze"},
                confidence=0.75,
            ),
            Capability(
                capability_id="mujoco:motion:greet",
                adapter_id=self.adapter_id,
                embodiment="mujoco",
                modality="motion",
                channels=["head", "gesture"],
                semantic_tags=["greet", "acknowledge"],
                input_schema={"command_type": "play_motion"},
                timing_profile={"duration_ms": 1200},
                confidence=0.7,
            ),
        ]

    async def execute(self, command: RobotCommand) -> AdapterResult:
        """Execute or simulate one command."""
        await self.activate()
        started_at_ms = now_ms()
        self._active_commands.add(command.command_id)
        try:
            if command.command_type == "safe_idle":
                return _result(
                    command,
                    started_at_ms,
                    telemetry={
                        "safe_idle": True,
                        "source_capability": command.capability_id,
                    },
                )
            telemetry = {"simulated": self.execute_command is None and self.backend is None}
            if self.execute_command is not None:
                telemetry.update(await self.execute_command(command))
            elif self.backend is not None:
                telemetry.update(_apply_backend_command(command, self.backend))
            return _result(command, started_at_ms, telemetry=telemetry)
        finally:
            self._active_commands.discard(command.command_id)

    async def cancel(self, command_id: str, reason: str) -> AdapterResult:
        """Cancel one simulation command."""
        self._active_commands.discard(command_id)
        return AdapterResult(
            command_id=command_id,
            adapter_id=self.adapter_id,
            status=AdapterResultStatus.CANCELLED,
            error_message=reason,
        )

    async def state(self) -> AdapterState:
        """Return simulation adapter state."""
        return AdapterState(
            adapter_id=self.adapter_id,
            lifecycle=self._lifecycle,
            mode=RuntimeMode.SIMULATION,
            active_command_ids=sorted(self._active_commands),
            metadata={"options": dict(self.context.options)},
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


__all__ = ["ExecuteCommand", "MujocoAdapter"]


def _apply_backend_command(command: RobotCommand, backend: Any) -> dict[str, object]:
    payload = dict(command.payload)
    target = _target_payload(payload)
    updated: list[str] = []
    head_pose = _head_pose(payload, target)
    body_yaw = _body_yaw(payload, target)
    antennas = _antennas(payload, target)
    if head_pose is not None:
        setattr(backend, "target_head_pose", head_pose)
        setattr(backend, "ik_required", True)
        updated.append("target_head_pose")
    if body_yaw is not None:
        setattr(backend, "target_body_yaw", body_yaw)
        setattr(backend, "ik_required", True)
        updated.append("target_body_yaw")
    if antennas is not None:
        setattr(backend, "target_antenna_joint_positions", antennas)
        updated.append("target_antenna_joint_positions")
    if not updated and command.command_type in {"play_motion", "set_gaze"}:
        setattr(backend, "target_head_pose", _pose_from_euler())
        setattr(backend, "ik_required", True)
        updated.append("target_head_pose")
    return {
        "backend": type(backend).__name__,
        "updated": updated,
        "command_type": command.command_type,
    }


def _target_payload(payload: dict[str, object]) -> dict[str, object]:
    target = payload.get("target")
    return dict(target) if isinstance(target, dict) else {}


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
            raise ValueError("head pose must be a 4x4 matrix")
        return pose
    pitch = _angle(payload, target, "pitch")
    roll = _angle(payload, target, "roll")
    yaw = _angle(payload, target, "yaw")
    if pitch is None and roll is None and yaw is None:
        return None
    return _pose_from_euler(
        roll=roll or 0.0,
        pitch=pitch or 0.0,
        yaw=yaw or 0.0,
    )


def _antennas(
    payload: dict[str, object],
    target: dict[str, object],
) -> np.ndarray | None:
    raw = payload.get("antennas") or target.get("antennas")
    if raw is not None:
        return _array(raw, expected_len=2)
    raw_deg = payload.get("antennas_deg") or target.get("antennas_deg")
    if raw_deg is not None:
        return np.radians(_array(raw_deg, expected_len=2))
    return None


def _body_yaw(
    payload: dict[str, object],
    target: dict[str, object],
) -> float | None:
    if payload.get("body_yaw") is not None:
        return float(payload["body_yaw"])
    if target.get("body_yaw") is not None:
        return float(target["body_yaw"])
    if payload.get("body_yaw_deg") is not None:
        return float(np.radians(float(payload["body_yaw_deg"])))
    if target.get("body_yaw_deg") is not None:
        return float(np.radians(float(target["body_yaw_deg"])))
    return None


def _angle(
    payload: dict[str, object],
    target: dict[str, object],
    name: str,
) -> float | None:
    rad_key = f"{name}_rad"
    deg_key = f"{name}_deg"
    if payload.get(rad_key) is not None:
        return float(payload[rad_key])
    if target.get(rad_key) is not None:
        return float(target[rad_key])
    if payload.get(deg_key) is not None:
        return float(np.radians(float(payload[deg_key])))
    if target.get(deg_key) is not None:
        return float(np.radians(float(target[deg_key])))
    return None


def _array(raw: object, *, expected_len: int) -> np.ndarray:
    values = np.array(list(raw), dtype=np.float64)  # type: ignore[arg-type]
    if values.shape != (expected_len,):
        raise ValueError(f"expected {expected_len} values")
    return values


def _pose_from_euler(
    *,
    roll: float = 0.0,
    pitch: float = 0.0,
    yaw: float = 0.0,
) -> np.ndarray:
    cx, sx = np.cos(roll), np.sin(roll)
    cy, sy = np.cos(pitch), np.sin(pitch)
    cz, sz = np.cos(yaw), np.sin(yaw)
    rotation = np.array(
        [
            [cz * cy, (cz * sy * sx) - (sz * cx), (cz * sy * cx) + (sz * sx)],
            [sz * cy, (sz * sy * sx) + (cz * cx), (sz * sy * cx) - (cz * sx)],
            [-sy, cy * sx, cy * cx],
        ],
        dtype=np.float64,
    )
    pose = np.eye(4, dtype=np.float64)
    pose[:3, :3] = rotation
    return pose
