"""Queueable dance, emotion, and goto moves for the runtime movement manager."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from numpy.typing import NDArray

from reachy_mini.motion.move import Move

logger = logging.getLogger(__name__)


def _neutral_result() -> tuple[NDArray[np.float64], NDArray[np.float64], float]:
    from reachy_mini.utils import create_head_pose

    return (
        create_head_pose(0, 0, 0, 0, 0, 0, degrees=True).astype(np.float64),
        np.array([0.0, 0.0], dtype=np.float64),
        0.0,
    )


class DanceQueueMove(Move):
    """Wrap one recorded dance move so it can live in the movement queue."""

    def __init__(self, move_name: str, recorded_moves: Any) -> None:
        self.dance_move = recorded_moves.get(move_name)
        self.move_name = move_name

    @property
    def duration(self) -> float:
        return float(self.dance_move.duration)

    def evaluate(
        self,
        t: float,
    ) -> tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        try:
            head_pose, antennas, body_yaw = self.dance_move.evaluate(t)
            if isinstance(antennas, tuple):
                antennas = np.array([antennas[0], antennas[1]], dtype=np.float64)
            elif antennas is not None:
                antennas = np.asarray(antennas, dtype=np.float64)
            if head_pose is not None:
                head_pose = np.asarray(head_pose, dtype=np.float64)
            return head_pose, antennas, body_yaw
        except Exception as exc:
            logger.error("Error evaluating dance '%s' at t=%s: %s", self.move_name, t, exc)
            return _neutral_result()


class EmotionQueueMove(Move):
    """Wrap one recorded emotion so it can live in the movement queue."""

    def __init__(self, emotion_name: str, recorded_moves: Any) -> None:
        self.emotion_move = recorded_moves.get(emotion_name)
        self.emotion_name = emotion_name

    @property
    def duration(self) -> float:
        return float(self.emotion_move.duration)

    def evaluate(
        self,
        t: float,
    ) -> tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        try:
            head_pose, antennas, body_yaw = self.emotion_move.evaluate(t)
            if isinstance(antennas, tuple):
                antennas = np.array([antennas[0], antennas[1]], dtype=np.float64)
            elif antennas is not None:
                antennas = np.asarray(antennas, dtype=np.float64)
            if head_pose is not None:
                head_pose = np.asarray(head_pose, dtype=np.float64)
            return head_pose, antennas, body_yaw
        except Exception as exc:
            logger.error(
                "Error evaluating emotion '%s' at t=%s: %s",
                self.emotion_name,
                t,
                exc,
            )
            return _neutral_result()


class GotoQueueMove(Move):
    """Queue-friendly goto interpolation matching the old conversation app."""

    def __init__(
        self,
        target_head_pose: NDArray[np.float64],
        start_head_pose: NDArray[np.float64] | None = None,
        target_antennas: tuple[float, float] = (0.0, 0.0),
        start_antennas: tuple[float, float] | None = None,
        target_body_yaw: float = 0.0,
        start_body_yaw: float | None = None,
        duration: float = 1.0,
    ) -> None:
        self._duration = float(duration)
        self.target_head_pose = np.asarray(target_head_pose, dtype=np.float64)
        self.start_head_pose = (
            np.asarray(start_head_pose, dtype=np.float64)
            if start_head_pose is not None
            else None
        )
        self.target_antennas = (float(target_antennas[0]), float(target_antennas[1]))
        self.start_antennas = (
            float(start_antennas[0]),
            float(start_antennas[1]),
        ) if start_antennas is not None else (0.0, 0.0)
        self.target_body_yaw = float(target_body_yaw)
        self.start_body_yaw = (
            float(start_body_yaw) if start_body_yaw is not None else 0.0
        )

    @property
    def duration(self) -> float:
        return self._duration

    def evaluate(
        self,
        t: float,
    ) -> tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        try:
            from reachy_mini.utils import create_head_pose
            from reachy_mini.utils.interpolation import linear_pose_interpolation

            t_clamped = max(0.0, min(1.0, t / self.duration))
            start_pose = (
                self.start_head_pose
                if self.start_head_pose is not None
                else create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            )
            head_pose = linear_pose_interpolation(
                start_pose,
                self.target_head_pose,
                t_clamped,
            ).astype(np.float64)
            antennas = np.array(
                [
                    self.start_antennas[0]
                    + (self.target_antennas[0] - self.start_antennas[0]) * t_clamped,
                    self.start_antennas[1]
                    + (self.target_antennas[1] - self.start_antennas[1]) * t_clamped,
                ],
                dtype=np.float64,
            )
            body_yaw = self.start_body_yaw + (
                self.target_body_yaw - self.start_body_yaw
            ) * t_clamped
            return head_pose, antennas, body_yaw
        except Exception as exc:
            logger.error("Error evaluating goto move at t=%s: %s", t, exc)
            return (
                self.target_head_pose.astype(np.float64),
                np.array(self.target_antennas, dtype=np.float64),
                self.target_body_yaw,
            )


__all__ = ["DanceQueueMove", "EmotionQueueMove", "GotoQueueMove"]
