"""Camera worker extracted from the legacy conversation app."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

import numpy as np
from numpy.typing import NDArray

from reachy_mini.utils.interpolation import linear_pose_interpolation

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ReactiveVisionEvent:
    """One lightweight reactive-vision event emitted beside the tracking hot path."""

    name: str
    source: str = "reactive_vision"
    ts_monotonic: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class CameraWorker:
    """Thread-safe camera worker with optional face-tracking offsets."""

    def __init__(self, reachy_mini: Any, head_tracker: Any = None) -> None:
        self.reachy_mini = reachy_mini
        self.head_tracker = head_tracker

        self.latest_frame: NDArray[np.uint8] | None = None
        self.frame_lock = threading.Lock()
        self._process_lock = threading.Lock()
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
        self._reactive_vision_listeners: list[Callable[[ReactiveVisionEvent], None]] = []
        self._reactive_vision_listener_lock = threading.Lock()
        self._reactive_target_id = "primary"
        self._reactive_person_visible = False
        self._reactive_attention_active = False

    def add_reactive_vision_listener(
        self,
        listener: Any,
    ) -> None:
        """Register one lightweight listener for reactive-vision events."""
        if not callable(listener):
            return
        with self._reactive_vision_listener_lock:
            if listener not in self._reactive_vision_listeners:
                self._reactive_vision_listeners.append(listener)

    def remove_reactive_vision_listener(
        self,
        listener: Any,
    ) -> None:
        """Remove one previously registered reactive-vision listener."""
        with self._reactive_vision_listener_lock:
            self._reactive_vision_listeners = [
                item for item in self._reactive_vision_listeners if item is not listener
            ]

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

    def ingest_external_frame(self, frame: NDArray[np.uint8] | None) -> None:
        """Inject one external BGR frame into the tracking pipeline."""
        if frame is None:
            return

        normalized_frame = np.ascontiguousarray(frame)
        if normalized_frame.ndim != 3 or normalized_frame.shape[2] != 3:
            return

        with self.frame_lock:
            self.latest_frame = normalized_frame.copy()

        with self._process_lock:
            self._process_frame(
                frame=normalized_frame,
                current_time=time.time(),
                neutral_pose=np.eye(4),
            )

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
                    with self._process_lock:
                        self._process_frame(
                            frame=frame,
                            current_time=current_time,
                            neutral_pose=neutral_pose,
                        )

                time.sleep(1 / 30)
            except Exception as exc:  # pragma: no cover - defensive runtime loop
                logger.warning("Camera worker loop error: %s", exc)
                time.sleep(0.1)

    def _process_frame(
        self,
        *,
        frame: NDArray[np.uint8],
        current_time: float,
        neutral_pose: NDArray[np.float64],
    ) -> None:
        """Process one frame for both low-latency tracking and lightweight event emission."""
        if (
            self.previous_head_tracking_state
            and not self.is_head_tracking_enabled
        ):
            self.last_face_detected_time = current_time
            self.interpolation_start_time = None
            self.interpolation_start_pose = None
            self._emit_attention_released(
                reason="disabled",
                return_to_center=False,
            )

        self.previous_head_tracking_state = self.is_head_tracking_enabled

        if self.is_head_tracking_enabled and self.head_tracker is not None:
            eye_center, _, confidence = self._get_head_observation(frame)
            if eye_center is not None:
                self._handle_face_detected(
                    eye_center=eye_center,
                    current_time=current_time,
                    confidence=confidence,
                )
                return

        self._handle_face_lost(current_time, neutral_pose)

    def _handle_face_detected(
        self,
        *,
        eye_center: NDArray[np.float32],
        current_time: float,
        confidence: float | None,
    ) -> None:
        """Update tracking offsets and emit first-stage discrete attention events."""
        self.last_face_detected_time = current_time
        self.interpolation_start_time = None
        direction = self._resolve_attention_direction(eye_center)

        if not self._reactive_person_visible:
            self._reactive_person_visible = True
            self._emit_reactive_vision_event(
                "person_detected",
                target_id=self._reactive_target_id,
                confidence=float(confidence or 0.0),
                direction=direction,
                tracking_enabled=bool(self.is_head_tracking_enabled),
            )

        if not self._reactive_attention_active:
            self._reactive_attention_active = True
            self._emit_reactive_vision_event(
                "attention_acquired",
                target_id=self._reactive_target_id,
                direction=direction,
                tracking_enabled=bool(self.is_head_tracking_enabled),
            )

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

        if self._reactive_person_visible:
            self._reactive_person_visible = False
            self._emit_reactive_vision_event(
                "person_lost",
                target_id=self._reactive_target_id,
                lost_for_ms=round(
                    max(current_time - self.last_face_detected_time, 0.0) * 1000.0,
                    1,
                ),
                return_to_center=True,
            )
        self._emit_attention_released(
            reason="lost",
            return_to_center=True,
        )

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

    def _emit_attention_released(
        self,
        *,
        reason: str,
        return_to_center: bool,
    ) -> None:
        """Emit at most one attention release until the next acquisition."""
        if not self._reactive_attention_active:
            return
        self._reactive_attention_active = False
        self._emit_reactive_vision_event(
            "attention_released",
            target_id=self._reactive_target_id,
            reason=str(reason or "").strip() or "unknown",
            return_to_center=bool(return_to_center),
        )

    def _emit_reactive_vision_event(
        self,
        name: str,
        **metadata: Any,
    ) -> None:
        """Emit one lightweight event without blocking the tracking hot path."""
        with self._reactive_vision_listener_lock:
            listeners = list(self._reactive_vision_listeners)
        if not listeners:
            return

        event = ReactiveVisionEvent(
            name=str(name or "").strip(),
            ts_monotonic=time.monotonic(),
            metadata={
                "source": "reactive_vision",
                **dict(metadata),
            },
        )
        for listener in listeners:
            try:
                listener(event)
            except Exception as exc:  # pragma: no cover - defensive runtime callback
                logger.warning("Reactive vision listener failed: %s", exc)

    def _get_head_observation(
        self,
        frame: NDArray[np.uint8],
    ) -> tuple[NDArray[np.float32] | None, float | None, float | None]:
        """Read one tracker observation while tolerating older tracker interfaces."""
        tracker = self.head_tracker
        if tracker is None:
            return None, None, None

        if hasattr(tracker, "get_head_observation"):
            result = tracker.get_head_observation(frame)
        else:
            result = tracker.get_head_position(frame)

        if not isinstance(result, tuple):
            return None, None, None
        if len(result) >= 3:
            return result[0], result[1], result[2]
        if len(result) == 2:
            return result[0], result[1], None
        return None, None, None

    @staticmethod
    def _resolve_attention_direction(
        eye_center: NDArray[np.float32],
        threshold: float = 0.25,
    ) -> str:
        """Bucket one normalized target center into a stable front-facing direction."""
        x = float(eye_center[0])
        y = float(eye_center[1])
        if abs(x) >= abs(y) and abs(x) >= threshold:
            return "left" if x < 0.0 else "right"
        if abs(y) >= threshold:
            return "up" if y < 0.0 else "down"
        return "front"
