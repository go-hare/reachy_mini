"""Full movement manager extracted from the conversation app.

This runtime keeps a single real-time control loop that fuses:
- one primary move queue (goto / emotion / dance / breathing)
- additive secondary offsets (speech sway / face tracking)
- listening-aware antenna freeze and blend-back

It intentionally mirrors the old conversation-app behaviour instead of using
`ReachyMini.play_move(...)` as a lightweight shortcut.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from reachy_mini.motion.move import Move
from reachy_mini.utils import create_head_pose
from reachy_mini.utils.interpolation import (
    compose_world_offset,
    linear_pose_interpolation,
)

if TYPE_CHECKING:
    from reachy_mini.reachy_mini import ReachyMini

logger = logging.getLogger(__name__)

CONTROL_LOOP_FREQUENCY_HZ = 60.0
FullBodyPose = tuple[NDArray[np.float64], tuple[float, float], float]


class BreathingMove(Move):
    """Continuous breathing animation used while idle."""

    def __init__(
        self,
        interpolation_start_pose: NDArray[np.float64],
        interpolation_start_antennas: tuple[float, float],
        interpolation_duration: float = 1.0,
    ) -> None:
        self.interpolation_start_pose = interpolation_start_pose
        self.interpolation_start_antennas = np.array(
            interpolation_start_antennas,
            dtype=np.float64,
        )
        self.interpolation_duration = interpolation_duration

        self.neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        self.neutral_antennas = np.array([-0.1745, 0.1745], dtype=np.float64)

        self.breathing_z_amplitude = 0.005
        self.breathing_frequency = 0.1
        self.antenna_sway_amplitude = float(np.deg2rad(15.0))
        self.antenna_frequency = 0.5

    @property
    def duration(self) -> float:
        return float("inf")

    def evaluate(
        self,
        t: float,
    ) -> tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        if t < self.interpolation_duration:
            interpolation_t = t / self.interpolation_duration
            head_pose = linear_pose_interpolation(
                self.interpolation_start_pose,
                self.neutral_head_pose,
                interpolation_t,
            )
            antennas = (
                (1.0 - interpolation_t) * self.interpolation_start_antennas
                + interpolation_t * self.neutral_antennas
            ).astype(np.float64)
        else:
            breathing_time = t - self.interpolation_duration
            z_offset = self.breathing_z_amplitude * np.sin(
                2.0 * np.pi * self.breathing_frequency * breathing_time
            )
            head_pose = create_head_pose(
                x=0,
                y=0,
                z=z_offset,
                roll=0,
                pitch=0,
                yaw=0,
                degrees=True,
                mm=False,
            )
            antenna_sway = self.antenna_sway_amplitude * np.sin(
                2.0 * np.pi * self.antenna_frequency * breathing_time
            )
            antennas = np.array([antenna_sway, -antenna_sway], dtype=np.float64)

        return head_pose.astype(np.float64), antennas, 0.0


def combine_full_body(
    primary_pose: FullBodyPose,
    secondary_pose: FullBodyPose,
) -> FullBodyPose:
    """Compose a primary pose with secondary world-frame offsets."""

    primary_head, primary_antennas, primary_body_yaw = primary_pose
    secondary_head, secondary_antennas, secondary_body_yaw = secondary_pose

    combined_head = compose_world_offset(
        primary_head,
        secondary_head,
        reorthonormalize=False,
    ).astype(np.float64)
    combined_antennas = (
        primary_antennas[0] + secondary_antennas[0],
        primary_antennas[1] + secondary_antennas[1],
    )
    combined_body_yaw = primary_body_yaw + secondary_body_yaw
    return combined_head, combined_antennas, combined_body_yaw


def clone_full_body_pose(pose: FullBodyPose) -> FullBodyPose:
    """Clone a full-body pose tuple."""

    head, antennas, body_yaw = pose
    return head.copy(), (float(antennas[0]), float(antennas[1])), float(body_yaw)


@dataclass
class MovementState:
    """State owned by the worker thread."""

    current_move: Move | None = None
    move_start_time: float | None = None
    last_activity_time: float = 0.0
    surface_offsets: tuple[float, float, float, float, float, float] = (
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
    speech_offsets: tuple[float, float, float, float, float, float] = (
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
    face_tracking_offsets: tuple[float, float, float, float, float, float] = (
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
    last_primary_pose: FullBodyPose | None = None

    def update_activity(self) -> None:
        self.last_activity_time = time.monotonic()


@dataclass
class LoopFrequencyStats:
    """Rolling telemetry for the control loop."""

    mean: float = 0.0
    m2: float = 0.0
    min_freq: float = float("inf")
    count: int = 0
    last_freq: float = 0.0
    potential_freq: float = 0.0

    def reset(self) -> None:
        self.mean = 0.0
        self.m2 = 0.0
        self.min_freq = float("inf")
        self.count = 0


class MovementManager:
    """Full motion queue manager used by the runtime tools."""

    def __init__(
        self,
        current_robot: "ReachyMini | Any",
        camera_worker: Any = None,
    ) -> None:
        self.current_robot = current_robot
        self.camera_worker = camera_worker
        self._now = time.monotonic

        self.state = MovementState()
        self.state.last_activity_time = self._now()
        neutral_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        self.state.last_primary_pose = (neutral_pose.astype(np.float64), (0.0, 0.0), 0.0)

        self.move_queue: deque[Move] = deque()
        self.idle_inactivity_delay = 0.3
        self.target_frequency = CONTROL_LOOP_FREQUENCY_HZ
        self.target_period = 1.0 / self.target_frequency

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._is_listening = False
        self._last_commanded_pose: FullBodyPose = clone_full_body_pose(
            self.state.last_primary_pose
        )
        self._listening_antennas: tuple[float, float] = self._last_commanded_pose[1]
        self._antenna_unfreeze_blend = 1.0
        self._antenna_blend_duration = 0.4
        self._last_listening_blend_time = self._now()
        self._breathing_active = False
        self._listening_debounce_s = 0.15
        self._last_listening_toggle_time = self._now()
        self._last_set_target_err = 0.0
        self._set_target_err_interval = 1.0
        self._set_target_err_suppressed = 0
        self._cached_secondary_offsets: tuple[float, ...] = ()
        self._cached_secondary_pose: FullBodyPose = (
            np.eye(4, dtype=np.float64),
            (0.0, 0.0),
            0.0,
        )

        self._command_queue: Queue[tuple[str, Any]] = Queue()
        self._surface_offsets_lock = threading.Lock()
        self._pending_surface_offsets: tuple[float, float, float, float, float, float] = (
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
        self._surface_offsets_dirty = False
        self._speech_offsets_lock = threading.Lock()
        self._pending_speech_offsets: tuple[float, float, float, float, float, float] = (
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
        self._speech_offsets_dirty = False

        self._face_offsets_lock = threading.Lock()
        self._pending_face_offsets: tuple[float, float, float, float, float, float] = (
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
        self._face_offsets_dirty = False

        self._shared_state_lock = threading.Lock()
        self._shared_last_activity_time = self.state.last_activity_time
        self._shared_is_listening = self._is_listening
        self._status_lock = threading.Lock()
        self._freq_stats = LoopFrequencyStats()
        self._freq_snapshot = LoopFrequencyStats()

    def queue_move(self, move: Move) -> None:
        """Queue a primary move."""

        self._command_queue.put(("queue_move", move))

    def clear_move_queue(self) -> None:
        """Stop the active move and discard queued moves."""

        self._command_queue.put(("clear_queue", None))

    def set_speech_offsets(
        self,
        offsets: tuple[float, float, float, float, float, float],
    ) -> None:
        """Update speech-driven secondary offsets."""

        with self._speech_offsets_lock:
            self._pending_speech_offsets = offsets
            self._speech_offsets_dirty = True

    def set_surface_offsets(
        self,
        offsets: tuple[float, float, float, float, float, float],
    ) -> None:
        """Update low-amplitude surface-expression secondary offsets."""

        with self._surface_offsets_lock:
            self._pending_surface_offsets = offsets
            self._surface_offsets_dirty = True

    def set_face_tracking_offsets(
        self,
        offsets: tuple[float, float, float, float, float, float],
    ) -> None:
        """Allow direct face-tracking updates when no camera worker is used."""

        with self._face_offsets_lock:
            self._pending_face_offsets = offsets
            self._face_offsets_dirty = True

    def set_moving_state(self, duration: float) -> None:
        """Mark the robot as active for a movement duration."""

        self._command_queue.put(("set_moving_state", duration))

    def mark_activity(self) -> None:
        """Refresh the idle timer."""

        self._command_queue.put(("mark_activity", None))

    def is_idle(self) -> bool:
        """Whether the robot has been idle longer than the configured delay."""

        with self._shared_state_lock:
            last_activity = self._shared_last_activity_time
            listening = self._shared_is_listening

        if listening:
            return False
        return self._now() - last_activity >= self.idle_inactivity_delay

    def set_listening(self, listening: bool) -> None:
        """Toggle listening mode."""

        with self._shared_state_lock:
            if self._shared_is_listening == listening:
                return
        self._command_queue.put(("set_listening", listening))

    def _poll_signals(self, current_time: float) -> None:
        self._apply_pending_offsets()
        while True:
            try:
                command, payload = self._command_queue.get_nowait()
            except Empty:
                break
            self._handle_command(command, payload, current_time)

    def _apply_pending_offsets(self) -> None:
        surface_offsets: tuple[float, float, float, float, float, float] | None = None
        with self._surface_offsets_lock:
            if self._surface_offsets_dirty:
                surface_offsets = self._pending_surface_offsets
                self._surface_offsets_dirty = False

        if surface_offsets is not None:
            self.state.surface_offsets = surface_offsets

        speech_offsets: tuple[float, float, float, float, float, float] | None = None
        with self._speech_offsets_lock:
            if self._speech_offsets_dirty:
                speech_offsets = self._pending_speech_offsets
                self._speech_offsets_dirty = False

        if speech_offsets is not None:
            self.state.speech_offsets = speech_offsets
            self.state.update_activity()

        face_offsets: tuple[float, float, float, float, float, float] | None = None
        with self._face_offsets_lock:
            if self._face_offsets_dirty:
                face_offsets = self._pending_face_offsets
                self._face_offsets_dirty = False

        if face_offsets is not None:
            self.state.face_tracking_offsets = face_offsets
            self.state.update_activity()

    def _handle_command(self, command: str, payload: Any, current_time: float) -> None:
        _ = current_time
        if command == "queue_move":
            if isinstance(payload, Move):
                self.move_queue.append(payload)
                self.state.update_activity()
            else:
                logger.warning("Ignored queue_move payload: %r", payload)
            return

        if command == "clear_queue":
            self.move_queue.clear()
            self.state.current_move = None
            self.state.move_start_time = None
            self._breathing_active = False
            logger.info("Cleared move queue")
            return

        if command == "set_moving_state":
            try:
                float(payload)
            except (TypeError, ValueError):
                logger.warning("Invalid moving-state duration: %r", payload)
                return
            self.state.update_activity()
            return

        if command == "mark_activity":
            self.state.update_activity()
            return

        if command == "set_listening":
            desired_state = bool(payload)
            now = self._now()
            if now - self._last_listening_toggle_time < self._listening_debounce_s:
                return
            self._last_listening_toggle_time = now
            if self._is_listening == desired_state:
                return

            self._is_listening = desired_state
            self._last_listening_blend_time = now
            if desired_state:
                self._listening_antennas = (
                    float(self._last_commanded_pose[1][0]),
                    float(self._last_commanded_pose[1][1]),
                )
                self._antenna_unfreeze_blend = 0.0
            else:
                self._antenna_unfreeze_blend = 0.0
            self.state.update_activity()
            return

        logger.warning("Unknown movement-manager command: %s", command)

    def _publish_shared_state(self) -> None:
        with self._shared_state_lock:
            self._shared_last_activity_time = self.state.last_activity_time
            self._shared_is_listening = self._is_listening

    def _manage_move_queue(self, current_time: float) -> None:
        current_move = self.state.current_move
        if current_move is None or (
            self.state.move_start_time is not None
            and current_time - self.state.move_start_time >= current_move.duration
        ):
            self.state.current_move = None
            self.state.move_start_time = None
            if self.move_queue:
                self.state.current_move = self.move_queue.popleft()
                self.state.move_start_time = current_time
                self._breathing_active = isinstance(
                    self.state.current_move,
                    BreathingMove,
                )

    def _manage_breathing(self, current_time: float) -> None:
        if (
            self.state.current_move is None
            and not self.move_queue
            and not self._is_listening
            and not self._breathing_active
        ):
            idle_for = current_time - self.state.last_activity_time
            if idle_for >= self.idle_inactivity_delay:
                try:
                    _, current_antennas = self.current_robot.get_current_joint_positions()
                    current_head_pose = self.current_robot.get_current_head_pose()
                    self._breathing_active = True
                    self.state.update_activity()
                    self.move_queue.append(
                        BreathingMove(
                            interpolation_start_pose=current_head_pose,
                            interpolation_start_antennas=(
                                float(current_antennas[0]),
                                float(current_antennas[1]),
                            ),
                            interpolation_duration=1.0,
                        )
                    )
                except Exception as exc:
                    self._breathing_active = False
                    logger.error("Failed to start breathing: %s", exc)

        if isinstance(self.state.current_move, BreathingMove) and self.move_queue:
            self.state.current_move = None
            self.state.move_start_time = None
            self._breathing_active = False

        if self.state.current_move is not None and not isinstance(
            self.state.current_move,
            BreathingMove,
        ):
            self._breathing_active = False

    def _get_primary_pose(self, current_time: float) -> FullBodyPose:
        if self.state.current_move is not None and self.state.move_start_time is not None:
            move_time = current_time - self.state.move_start_time
            head, antennas, body_yaw = self.state.current_move.evaluate(move_time)

            if head is None:
                head = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            if antennas is None:
                antennas = np.array([-0.1745, 0.1745], dtype=np.float64)
            if body_yaw is None:
                body_yaw = 0.0

            primary_pose: FullBodyPose = (
                head.astype(np.float64).copy(),
                (float(antennas[0]), float(antennas[1])),
                float(body_yaw),
            )
            self.state.last_primary_pose = clone_full_body_pose(primary_pose)
            return primary_pose

        if self.state.last_primary_pose is not None:
            return clone_full_body_pose(self.state.last_primary_pose)

        neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        primary_pose = (neutral_head_pose.astype(np.float64), (0.0, 0.0), 0.0)
        self.state.last_primary_pose = clone_full_body_pose(primary_pose)
        return primary_pose

    def _get_secondary_pose(self) -> FullBodyPose:
        current_offsets = (
            self.state.surface_offsets[0]
            + self.state.speech_offsets[0]
            + self.state.face_tracking_offsets[0],
            self.state.surface_offsets[1]
            + self.state.speech_offsets[1]
            + self.state.face_tracking_offsets[1],
            self.state.surface_offsets[2]
            + self.state.speech_offsets[2]
            + self.state.face_tracking_offsets[2],
            self.state.surface_offsets[3]
            + self.state.speech_offsets[3]
            + self.state.face_tracking_offsets[3],
            self.state.surface_offsets[4]
            + self.state.speech_offsets[4]
            + self.state.face_tracking_offsets[4],
            self.state.surface_offsets[5]
            + self.state.speech_offsets[5]
            + self.state.face_tracking_offsets[5],
        )
        if current_offsets == self._cached_secondary_offsets:
            return self._cached_secondary_pose

        secondary_head_pose = create_head_pose(
            x=current_offsets[0],
            y=current_offsets[1],
            z=current_offsets[2],
            roll=current_offsets[3],
            pitch=current_offsets[4],
            yaw=current_offsets[5],
            degrees=False,
            mm=False,
        ).astype(np.float64)
        self._cached_secondary_offsets = current_offsets
        self._cached_secondary_pose = (secondary_head_pose, (0.0, 0.0), 0.0)
        return self._cached_secondary_pose

    def _compose_full_body_pose(self, current_time: float) -> FullBodyPose:
        primary_pose = self._get_primary_pose(current_time)
        secondary_pose = self._get_secondary_pose()
        return combine_full_body(primary_pose, secondary_pose)

    def _update_primary_motion(self, current_time: float) -> None:
        self._manage_move_queue(current_time)
        self._manage_breathing(current_time)

    def _calculate_blended_antennas(
        self,
        target_antennas: tuple[float, float],
    ) -> tuple[float, float]:
        now = self._now()
        listening = self._is_listening
        listening_antennas = self._listening_antennas
        blend = self._antenna_unfreeze_blend
        last_update = self._last_listening_blend_time
        self._last_listening_blend_time = now

        if listening:
            antennas_cmd = listening_antennas
            new_blend = 0.0
        else:
            dt = max(0.0, now - last_update)
            if self._antenna_blend_duration <= 0:
                new_blend = 1.0
            else:
                new_blend = min(1.0, blend + dt / self._antenna_blend_duration)
            antennas_cmd = (
                listening_antennas[0] * (1.0 - new_blend)
                + target_antennas[0] * new_blend,
                listening_antennas[1] * (1.0 - new_blend)
                + target_antennas[1] * new_blend,
            )

        if listening:
            self._antenna_unfreeze_blend = 0.0
        else:
            self._antenna_unfreeze_blend = new_blend
            if new_blend >= 1.0:
                self._listening_antennas = (
                    float(target_antennas[0]),
                    float(target_antennas[1]),
                )

        return antennas_cmd

    def _issue_control_command(
        self,
        head: NDArray[np.float64],
        antennas: tuple[float, float],
        body_yaw: float,
    ) -> None:
        try:
            self.current_robot.set_target(
                head=head,
                antennas=antennas,
                body_yaw=body_yaw,
            )
        except Exception as exc:
            now = self._now()
            if now - self._last_set_target_err >= self._set_target_err_interval:
                message = f"Failed to set robot target: {exc}"
                if self._set_target_err_suppressed:
                    message += (
                        f" (suppressed {self._set_target_err_suppressed} repeats)"
                    )
                    self._set_target_err_suppressed = 0
                logger.error(message)
                self._last_set_target_err = now
            else:
                self._set_target_err_suppressed += 1
            return

        with self._status_lock:
            self._last_commanded_pose = clone_full_body_pose((head, antennas, body_yaw))

    def _update_frequency_stats(
        self,
        loop_start: float,
        prev_loop_start: float,
        stats: LoopFrequencyStats,
    ) -> LoopFrequencyStats:
        period = loop_start - prev_loop_start
        if period > 0:
            stats.last_freq = 1.0 / period
            stats.count += 1
            delta = stats.last_freq - stats.mean
            stats.mean += delta / stats.count
            stats.m2 += delta * (stats.last_freq - stats.mean)
            stats.min_freq = min(stats.min_freq, stats.last_freq)
        return stats

    def _schedule_next_tick(
        self,
        loop_start: float,
        stats: LoopFrequencyStats,
    ) -> tuple[float, LoopFrequencyStats]:
        computation_time = self._now() - loop_start
        stats.potential_freq = (
            1.0 / computation_time if computation_time > 0 else float("inf")
        )
        sleep_time = max(0.0, self.target_period - computation_time)
        return sleep_time, stats

    def _record_frequency_snapshot(self, stats: LoopFrequencyStats) -> None:
        with self._status_lock:
            self._freq_snapshot = LoopFrequencyStats(
                mean=stats.mean,
                m2=stats.m2,
                min_freq=stats.min_freq,
                count=stats.count,
                last_freq=stats.last_freq,
                potential_freq=stats.potential_freq,
            )

    def _maybe_log_frequency(
        self,
        loop_count: int,
        print_interval_loops: int,
        stats: LoopFrequencyStats,
    ) -> None:
        if loop_count % print_interval_loops != 0 or stats.count == 0:
            return

        variance = stats.m2 / stats.count if stats.count > 0 else 0.0
        lowest = stats.min_freq if stats.min_freq != float("inf") else 0.0
        logger.debug(
            "Loop freq avg=%.2fHz variance=%.4f min=%.2fHz last=%.2fHz potential=%.2fHz target=%.1fHz",
            stats.mean,
            variance,
            lowest,
            stats.last_freq,
            stats.potential_freq,
            self.target_frequency,
        )
        stats.reset()

    def _update_face_tracking(self) -> None:
        if self.camera_worker is None:
            self.state.face_tracking_offsets = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            return

        try:
            offsets = self.camera_worker.get_face_tracking_offsets()
        except Exception as exc:
            logger.warning("Failed to read face-tracking offsets: %s", exc)
            offsets = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.state.face_tracking_offsets = offsets

    def start(self) -> None:
        """Start the worker thread."""

        if self._thread is not None and self._thread.is_alive():
            logger.warning("Movement manager already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.working_loop, daemon=True)
        self._thread.start()
        logger.debug("Movement manager started")

    def stop(self) -> None:
        """Stop the worker thread and reset the robot to neutral."""

        if self._thread is None or not self._thread.is_alive():
            return

        self.clear_move_queue()
        self._stop_event.set()
        self._thread.join()
        self._thread = None

        try:
            neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            self.current_robot.goto_target(
                head=neutral_head_pose,
                antennas=[-0.1745, 0.1745],
                duration=2.0,
                body_yaw=0.0,
            )
        except Exception as exc:
            logger.error("Failed to reset robot to neutral: %s", exc)

    def get_status(self) -> dict[str, Any]:
        """Return a small runtime snapshot."""

        with self._status_lock:
            pose_snapshot = clone_full_body_pose(self._last_commanded_pose)
            freq_snapshot = LoopFrequencyStats(
                mean=self._freq_snapshot.mean,
                m2=self._freq_snapshot.m2,
                min_freq=self._freq_snapshot.min_freq,
                count=self._freq_snapshot.count,
                last_freq=self._freq_snapshot.last_freq,
                potential_freq=self._freq_snapshot.potential_freq,
            )

        return {
            "queue_size": len(self.move_queue),
            "is_listening": self._is_listening,
            "breathing_active": self._breathing_active,
            "last_commanded_pose": {
                "head": pose_snapshot[0].tolist(),
                "antennas": pose_snapshot[1],
                "body_yaw": pose_snapshot[2],
            },
            "loop_frequency": {
                "last": freq_snapshot.last_freq,
                "mean": freq_snapshot.mean,
                "min": freq_snapshot.min_freq,
                "potential": freq_snapshot.potential_freq,
                "samples": freq_snapshot.count,
            },
        }

    def working_loop(self) -> None:
        """Main real-time control loop."""

        logger.debug("Starting movement control loop")
        loop_count = 0
        prev_loop_start = self._now()
        print_interval_loops = max(1, int(self.target_frequency * 2))
        freq_stats = self._freq_stats

        while not self._stop_event.is_set():
            loop_start = self._now()
            loop_count += 1

            if loop_count > 1:
                freq_stats = self._update_frequency_stats(
                    loop_start,
                    prev_loop_start,
                    freq_stats,
                )
            prev_loop_start = loop_start

            self._poll_signals(loop_start)
            self._update_primary_motion(loop_start)
            self._update_face_tracking()
            head, antennas, body_yaw = self._compose_full_body_pose(loop_start)
            antennas_cmd = self._calculate_blended_antennas(antennas)
            self._issue_control_command(head, antennas_cmd, body_yaw)

            sleep_time, freq_stats = self._schedule_next_tick(loop_start, freq_stats)
            self._publish_shared_state()
            self._record_frequency_snapshot(freq_stats)
            self._maybe_log_frequency(loop_count, print_interval_loops, freq_stats)
            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.debug("Movement control loop stopped")


__all__ = [
    "BreathingMove",
    "CONTROL_LOOP_FREQUENCY_HZ",
    "LoopFrequencyStats",
    "MovementManager",
    "MovementState",
    "clone_full_body_pose",
    "combine_full_body",
]
