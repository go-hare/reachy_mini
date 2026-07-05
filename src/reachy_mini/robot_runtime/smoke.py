"""Executable RobotRuntime smoke and external preflight checks."""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import shutil
from dataclasses import dataclass, field
from typing import Literal

from reachy_mini.robot_runtime.adapters.avatar3d import Avatar3DAdapter
from reachy_mini.robot_runtime.adapters.mujoco import MujocoAdapter
from reachy_mini.robot_runtime.adapters.ros2 import ROS2Adapter
from reachy_mini.robot_runtime.config import RobotRuntimeConfig
from reachy_mini.robot_runtime.contracts import RobotCommand, RuntimeMode
from reachy_mini.robot_runtime.runtime import RobotRuntime
from reachy_mini.robot_runtime.tools import RobotIntentTools

SmokeStatus = Literal["passed", "failed", "skipped", "partial"]
DEFAULT_REACHY_DEVICE_GLOBS = ("/dev/tty.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*")


@dataclass(frozen=True, slots=True)
class SmokeCheckResult:
    """One smoke/preflight check result."""

    name: str
    status: SmokeStatus
    reason: str = ""
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly result."""
        return {
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class SmokeSuiteResult:
    """Aggregate RobotRuntime smoke/preflight result."""

    checks: tuple[SmokeCheckResult, ...]

    @property
    def status(self) -> SmokeStatus:
        """Return aggregate status without treating skipped external checks as pass."""
        if any(check.status == "failed" for check in self.checks):
            return "failed"
        if all(check.status == "skipped" for check in self.checks):
            return "skipped"
        if any(check.status == "skipped" for check in self.checks):
            return "partial"
        return "passed"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly aggregate payload."""
        return {"status": self.status, "checks": [check.to_dict() for check in self.checks]}


async def run_mujoco_facade_smoke() -> SmokeCheckResult:
    """Verify one generic intent can reach the MuJoCo adapter facade."""
    seen: list[RobotCommand] = []

    async def execute(command: RobotCommand) -> dict[str, object]:
        seen.append(command)
        return {"scene": "smoke"}

    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    try:
        await runtime.activate()
        await runtime.register_adapter(MujocoAdapter(execute_command=execute))
        result = await RobotIntentTools(runtime).emit_embodied_intent(
            intent_type="greet",
            modalities=["gesture"],
            turn_id="smoke_mujoco",
        )
    except Exception as exc:
        return _failed("mujoco_facade", exc)
    finally:
        await runtime.stop()
    if result.get("error"):
        return SmokeCheckResult(
            name="mujoco_facade",
            status="failed",
            reason=str(result["error"]),
            details={"result": result},
        )
    last_event = result["events"][-1]
    if last_event["event_type"] != "adapter_result" or last_event["status"] != "completed":
        return SmokeCheckResult(
            name="mujoco_facade",
            status="failed",
            reason="intent did not complete on adapter",
            details={"last_event": last_event},
        )
    return SmokeCheckResult(
        name="mujoco_facade",
        status="passed",
        details={
            "commands": len(seen),
            "adapter_id": last_event["payload"].get("adapter_id"),
        },
    )


async def run_avatar3d_frame_smoke() -> SmokeCheckResult:
    """Verify one generic intent produces a 3D avatar embodiment frame."""
    frames: list[dict[str, object]] = []

    async def publish_frame(frame: object) -> None:
        frames.append(
            {
                "action": getattr(frame, "action", ""),
                "target": getattr(frame, "target", ""),
                "turn_id": getattr(frame, "turn_id", ""),
            }
        )

    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.AVATAR_ONLY))
    try:
        await runtime.activate()
        await runtime.register_adapter(Avatar3DAdapter(publish_frame=publish_frame))
        result = await RobotIntentTools(runtime).emit_embodied_intent(
            intent_type="greet",
            modalities=["gesture"],
            turn_id="smoke_avatar3d",
        )
    except Exception as exc:
        return _failed("avatar3d_frame", exc)
    finally:
        await runtime.stop()
    if result.get("error"):
        return SmokeCheckResult(
            name="avatar3d_frame",
            status="failed",
            reason=str(result["error"]),
            details={"result": result},
        )
    if not frames:
        return SmokeCheckResult(
            name="avatar3d_frame",
            status="failed",
            reason="no avatar frame was published",
            details={"events": result["events"]},
        )
    return SmokeCheckResult(
        name="avatar3d_frame",
        status="passed",
        details={"frames": frames},
    )


async def run_ros2_bridge_preflight(
    *,
    ros2_executable: str | None = None,
) -> SmokeCheckResult:
    """Verify ROS2 CLI availability and ROS2 adapter message serialization."""
    executable = ros2_executable if ros2_executable is not None else shutil.which("ros2")
    if not executable:
        return SmokeCheckResult(
            name="ros2_bridge_preflight",
            status="skipped",
            reason="ros2 executable not found",
        )
    messages: list[dict[str, object]] = []

    async def publish_message(message: dict[str, object]) -> None:
        messages.append(message)

    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.HYBRID))
    try:
        await runtime.activate()
        await runtime.register_adapter(ROS2Adapter(publish_message=publish_message))
        result = await RobotIntentTools(runtime).emit_embodied_intent(
            intent_type="greet",
            modalities=["gesture"],
            turn_id="smoke_ros2",
        )
    except Exception as exc:
        return _failed("ros2_bridge_preflight", exc, executable=executable)
    finally:
        await runtime.stop()
    if result.get("error"):
        return SmokeCheckResult(
            name="ros2_bridge_preflight",
            status="failed",
            reason=str(result["error"]),
            details={"executable": executable, "result": result},
        )
    return SmokeCheckResult(
        name="ros2_bridge_preflight",
        status="passed",
        details={"executable": executable, "messages": messages},
    )


def run_reachy_hardware_preflight(
    *,
    device_globs: tuple[str, ...] = DEFAULT_REACHY_DEVICE_GLOBS,
    allow_hardware: bool = False,
) -> SmokeCheckResult:
    """Check whether a Reachy hardware smoke can be attempted safely."""
    device_paths = sorted(
        {path for pattern in device_globs for path in glob.glob(pattern)}
    )
    if not device_paths:
        return SmokeCheckResult(
            name="reachy_hardware_preflight",
            status="skipped",
            reason="no candidate Reachy serial device found",
            details={"device_globs": list(device_globs)},
        )
    if not allow_hardware:
        return SmokeCheckResult(
            name="reachy_hardware_preflight",
            status="skipped",
            reason="hardware candidate found but allow_hardware is false",
            details={"device_paths": device_paths},
        )
    return SmokeCheckResult(
        name="reachy_hardware_preflight",
        status="passed",
        details={"device_paths": device_paths},
    )


async def run_robot_runtime_smoke_suite(
    *,
    include_external: bool = True,
    allow_hardware: bool = False,
) -> SmokeSuiteResult:
    """Run local smoke checks plus optional external preflights."""
    checks = [
        await run_mujoco_facade_smoke(),
        await run_avatar3d_frame_smoke(),
    ]
    if include_external:
        checks.append(await run_ros2_bridge_preflight())
        checks.append(run_reachy_hardware_preflight(allow_hardware=allow_hardware))
    return SmokeSuiteResult(tuple(checks))


def main(argv: list[str] | None = None) -> int:
    """Run RobotRuntime smoke checks from ``python -m``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON output")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="skip ROS2 and hardware preflight checks",
    )
    parser.add_argument(
        "--allow-hardware",
        action="store_true",
        help="treat detected hardware as eligible for external smoke",
    )
    args = parser.parse_args(argv)
    suite = asyncio.run(
        run_robot_runtime_smoke_suite(
            include_external=not args.local_only,
            allow_hardware=args.allow_hardware,
        )
    )
    if args.json:
        print(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2))
    else:
        for check in suite.checks:
            suffix = f" - {check.reason}" if check.reason else ""
            print(f"{check.status.upper()} {check.name}{suffix}")
    return 1 if suite.status == "failed" else 0


def _failed(name: str, exc: Exception, **details: object) -> SmokeCheckResult:
    return SmokeCheckResult(
        name=name,
        status="failed",
        reason=f"{type(exc).__name__}: {exc}",
        details=details,
    )


if __name__ == "__main__":  # pragma: no cover - exercised via CLI manually
    raise SystemExit(main())


__all__ = [
    "DEFAULT_REACHY_DEVICE_GLOBS", "SmokeCheckResult", "SmokeStatus",
    "SmokeSuiteResult", "main", "run_avatar3d_frame_smoke",
    "run_mujoco_facade_smoke", "run_reachy_hardware_preflight",
    "run_robot_runtime_smoke_suite", "run_ros2_bridge_preflight",
]
