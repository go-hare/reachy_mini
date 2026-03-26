"""Camera worker extracted from the legacy conversation app."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np
from numpy.typing import NDArray

from reachy_mini.utils.interpolation import linear_pose_interpolation

logger = logging.getLogger(__name__)


class CameraWorker:
    """Thread-safe camera worker with optional face-tracking offsets."""

    def __init__(self, reachy_mini: Any, head_tracker: Any = None) -> None:
        self.reachy_mini = reachy_mini
        self.head_tracker = head_tracker

        self.latest_frame: NDArray[np.uint8] | None = None
        self.frame_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.is_head_tracking_enabled = True
        self.face_tracking_offsets: list[float] = [0.0] * 6
        self.face_tracking_lock = threading.Lock()

        self.last_face_detected_time: float | None = None
        self.interpolation_start_time: float | None = None
        self.interpolation_start_pose: NDArray[np.float32] | None = None
        self.face_lost_delay = 2.0
        self.interpolation_duration = 1.0
        self.previous_head_tracking_state = self.is_head_tracking_enabled

    def get_latest_frame(self) -> NDArray[np.uint8] | None:
        """Return a copy of the latest frame if one is available."""
        with self.frame_lock:
            if self.latest_frame is None:
                return None
            return self.latest_frame.copy()

    def get_face_tracking_offsets(
        self,
    ) -> tuple[float, float, float, float, float, float]:
        """Return the latest face-tracking offsets."""
        with self.face_tracking_lock:
            offsets = self.face_tracking_offsets
            return (
                offsets[0],
                offsets[1],
                offsets[2],
                offsets[3],
                offsets[4],
                offsets[5],
            )

    def set_head_tracking_enabled(self, enabled: bool) -> None:
        """Enable or disable head tracking."""
        self.is_head_tracking_enabled = enabled
        logger.info("Head tracking %s", "enabled" if enabled else "disabled")

    def start(self) -> None:
        """Start the camera polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.working_loop, daemon=True)
        self._thread.start()
        logger.debug("Camera worker started")

    def stop(self) -> None:
        """Stop the camera polling thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        logger.debug("Camera worker stopped")

    def working_loop(self) -> None:
        """Poll the latest frame and optional face-tracking offsets."""
        logger.debug("Starting camera working loop")
        neutral_pose = np.eye(4)
        self.previous_head_tracking_state = self.is_head_tracking_enabled

        while not self._stop_event.is_set():
            try:
                current_time = time.time()
                frame = self.reachy_mini.media.get_frame()

                if frame is not None:
                    with self.frame_lock:
                        self.latest_frame = frame

                    if (
                        self.previous_head_tracking_state
                        and not self.is_head_tracking_enabled
                    ):
                        self.last_face_detected_time = current_time
                        self.interpolation_start_time = None
                        self.interpolation_start_pose = None

                    self.previous_head_tracking_state = self.is_head_tracking_enabled

                    if (
                        self.is_head_tracking_enabled
                        and self.head_tracker is not None
                    ):
                        eye_center, _ = self.head_tracker.get_head_position(frame)

                        if eye_center is not None:
                            self.last_face_detected_time = current_time
                            self.interpolation_start_time = None

                            h, w, _ = frame.shape
                            target_yaw = (eye_center[0] - 0.5) * -0.8
                            target_pitch = (eye_center[1] - 0.5) * 0.4

                            with self.face_tracking_lock:
                                self.face_tracking_offsets = [
                                    0.0,
                                    0.0,
                                    0.0,
                                    0.0,
                                    target_pitch,
                                    target_yaw,
                                ]
                        else:
                            self._handle_face_lost(current_time, neutral_pose)
                    else:
                        self._handle_face_lost(current_time, neutral_pose)

                time.sleep(1 / 30)
            except Exception as exc:  # pragma: no cover - defensive runtime loop
                logger.warning("Camera worker loop error: %s", exc)
                time.sleep(0.1)

    def _handle_face_lost(
        self,
        current_time: float,
        neutral_pose: NDArray[np.float64],
    ) -> None:
        """Blend face-tracking offsets back to neutral after loss."""
        if self.last_face_detected_time is None:
            self.last_face_detected_time = current_time

        if current_time - self.last_face_detected_time < self.face_lost_delay:
            return

        if self.interpolation_start_time is None:
            self.interpolation_start_time = current_time
            current_offsets = self.get_face_tracking_offsets()
            self.interpolation_start_pose = np.eye(4)
            self.interpolation_start_pose[:3, :3] = np.eye(3)
            self.interpolation_start_pose[0, 3] = current_offsets[0]
            self.interpolation_start_pose[1, 3] = current_offsets[1]
            self.interpolation_start_pose[2, 3] = current_offsets[2]

        assert self.interpolation_start_time is not None
        assert self.interpolation_start_pose is not None

        alpha = min(
            (current_time - self.interpolation_start_time) / self.interpolation_duration,
            1.0,
        )
        pose = linear_pose_interpolation(
            self.interpolation_start_pose,
            neutral_pose,
            alpha,
        )

        with self.face_tracking_lock:
            self.face_tracking_offsets = [
                float(pose[0, 3]),
                float(pose[1, 3]),
                float(pose[2, 3]),
                0.0,
                0.0,
                0.0,
            ]
