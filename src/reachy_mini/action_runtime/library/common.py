"""Shared helpers for built-in v4 robot actions."""

from __future__ import annotations

import asyncio
import math
from typing import Any, Callable

import numpy as np
from scipy.spatial.transform import Rotation as R

from reachy_mini.action_runtime import ActionContext, ActionSpec, RobotAction
from reachy_mini.utils.interpolation import InterpolationTechnique

INTERPOLATION_VALUES = [item.value for item in InterpolationTechnique]


class BaseRobotAction(RobotAction):
    """Base action storing the originating action spec."""

    def __init__(
        self,
        spec: ActionSpec,
        *,
        required_locks: set[str],
        duration_s: float | None,
        priority: int,
        interruptible: bool,
    ) -> None:
        """Initialize a built-in robot action."""
        super().__init__(
            name=spec.name,
            required_locks=required_locks,
            duration_s=duration_s,
            priority=priority,
            interruptible=interruptible,
        )
        self.spec = spec


async def call_sdk(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a potentially blocking SDK helper from async action code."""
    return await asyncio.to_thread(func, *args, **kwargs)


def pose_from_euler(*, roll_deg: float = 0.0, pitch_deg: float = 0.0, yaw_deg: float = 0.0) -> np.ndarray:
    """Build a 4x4 head pose from Euler angles in degrees."""
    pose = np.eye(4)
    pose[:3, :3] = R.from_euler(
        "xyz",
        [roll_deg, pitch_deg, yaw_deg],
        degrees=True,
    ).as_matrix()
    return pose


def interpolation_from_param(value: str) -> InterpolationTechnique:
    """Convert an action parameter to the SDK interpolation enum."""
    return InterpolationTechnique(value)


def seconds(value: Any, default: float) -> float:
    """Parse a numeric seconds parameter."""
    if value is None:
        return default
    return float(value)


def degrees_to_radians(value: float) -> float:
    """Convert degrees to radians."""
    return math.radians(value)


async def checkpoint_sleep(context: ActionContext, duration_s: float) -> None:
    """Sleep in small slices so cancellation is observed quickly."""
    remaining = max(0.0, duration_s)
    while remaining > 0:
        await context.cancel_token.checkpoint()
        step = min(0.05, remaining)
        await asyncio.sleep(step)
        remaining -= step
