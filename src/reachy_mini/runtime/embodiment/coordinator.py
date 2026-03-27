"""Minimal coordinator that arbitrates surface and speech embodiment outputs."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Mapping

from reachy_mini.runtime.dance_emotion_moves import (
    DanceQueueMove,
    EmotionQueueMove,
    GotoQueueMove,
)
from reachy_mini.runtime.speech_driver import SpeechDriver
from reachy_mini.runtime.surface_driver import SurfaceDriver
from reachy_mini.utils import create_head_pose

_HEAD_DIRECTION_DELTAS: dict[str, tuple[int, int, int, int, int, int]] = {
    "left": (0, 0, 0, 0, 0, 40),
    "right": (0, 0, 0, 0, 0, -40),
    "up": (0, 0, 0, 0, -30, 0),
    "down": (0, 0, 0, 0, 30, 0),
    "front": (0, 0, 0, 0, 0, 0),
}

_EXPLICIT_MOTION_PRIORITIES: dict[str, int] = {
    "dance": 10,
    "emotion": 20,
    "move_head": 30,
}


@dataclass(slots=True, frozen=True)
class ExplicitMotionClaim:
    """Decision returned when one explicit motion asks for the body."""

    allowed: bool
    interrupted_kind: str | None = None
    blocking_kind: str | None = None


@dataclass(slots=True)
class EmbodimentCoordinator:
    """Coordinate thin embodiment drivers behind one stable entrypoint."""

    reachy_mini: Any | None = None
    movement_manager: Any | None = None
    camera_worker: Any | None = None
    motion_duration_s: float = 1.0
    surface_driver: SurfaceDriver | None = None
    speech_driver: SpeechDriver | None = None
    now_fn: Callable[[], float] = time.monotonic
    _current_phase: str = field(default="idle", init=False, repr=False)
    _current_surface_state: dict[str, Any] = field(
        default_factory=lambda: {"phase": "idle"},
        init=False,
        repr=False,
    )
    _desired_head_tracking_enabled: bool = field(default=False, init=False, repr=False)
    _head_tracking_active: bool = field(default=False, init=False, repr=False)
    _explicit_motion_until: float = field(default=0.0, init=False, repr=False)
    _explicit_motion_kind: str | None = field(default=None, init=False, repr=False)
    _explicit_motion_priority: int = field(default=0, init=False, repr=False)

    @property
    def current_phase(self) -> str:
        """Return the latest aggregate embodiment phase."""

        self._refresh_public_state()
        return self._current_phase

    @property
    def current_surface_state(self) -> dict[str, Any]:
        """Return the latest aggregate surface-state snapshot seen by the coordinator."""

        self._refresh_public_state()
        return dict(self._current_surface_state)

    def apply_surface_state(self, state: Mapping[str, Any] | None) -> str:
        """Apply one surface-state snapshot and clear speech motion when needed."""

        now = self._current_time()
        if self.surface_driver is not None:
            phase = self.surface_driver.apply_state(state)
            current_state = self.surface_driver.current_state
        else:
            current_state = self._normalize_surface_state(state)
            phase = str(current_state.get("phase", "idle") or "idle")

        self._current_phase = phase
        self._current_surface_state = current_state
        speech_driver = self.speech_driver
        if speech_driver is not None:
            speech_driver.apply_phase(phase)
        self._sync_head_tracking(now)
        return phase

    def feed_audio_delta(self, delta_b64: str) -> bool:
        """Forward one assistant audio delta into the speech driver."""

        speech_driver = self.speech_driver
        if speech_driver is None:
            return False
        return speech_driver.feed_audio_delta(delta_b64)

    def reset_speech_motion(self) -> bool:
        """Clear queued speech motion regardless of the current lifecycle phase."""

        speech_driver = self.speech_driver
        if speech_driver is None:
            return False
        return speech_driver.reset_speech_motion()

    def set_head_tracking(self, start: bool) -> str:
        """Enable or disable camera-worker head tracking."""

        camera_worker = self.camera_worker
        if camera_worker is None or not hasattr(camera_worker, "set_head_tracking_enabled"):
            return "Error: head_tracking requires a configured camera_worker"

        enabled = bool(start)
        self._desired_head_tracking_enabled = enabled
        now = self._current_time()
        explicit_active = self._explicit_motion_active(now)
        self._sync_head_tracking(now)
        if not enabled:
            return "Head tracking stopped"
        if explicit_active:
            return "Head tracking deferred until motion settles"
        return "Head tracking started"

    async def move_head(self, direction: str) -> str:
        """Queue a simple goto move for one named head direction."""

        reachy_mini = self.reachy_mini
        movement_manager = self.movement_manager
        if reachy_mini is None or movement_manager is None:
            return "Error: move_head requires a connected ReachyMini runtime"

        normalized_direction = str(direction or "").strip().lower()
        deltas = _HEAD_DIRECTION_DELTAS.get(normalized_direction)
        if deltas is None:
            return f"Error: Unknown direction '{direction}'"

        duration = max(float(self.motion_duration_s or 1.0), 0.1)
        claim = self._claim_explicit_motion("move_head", duration)
        if not claim.allowed:
            return self._format_deferred_message(
                action_label=f"head move {normalized_direction}",
                blocking_kind=claim.blocking_kind,
            )
        target = create_head_pose(*deltas, degrees=True)

        try:
            current_head_pose = await asyncio.to_thread(reachy_mini.get_current_head_pose)
            current_head_joints, current_antennas = await asyncio.to_thread(
                reachy_mini.get_current_joint_positions
            )
            start_body_yaw = float(current_head_joints[0]) if current_head_joints else 0.0
            goto_move = GotoQueueMove(
                target_head_pose=target,
                start_head_pose=current_head_pose,
                target_antennas=(0.0, 0.0),
                start_antennas=(
                    float(current_antennas[0]),
                    float(current_antennas[1]),
                ),
                target_body_yaw=0.0,
                start_body_yaw=start_body_yaw,
                duration=duration,
            )
            movement_manager.queue_move(goto_move)
            if hasattr(movement_manager, "set_moving_state"):
                movement_manager.set_moving_state(duration)
            elif hasattr(movement_manager, "mark_activity"):
                movement_manager.mark_activity()
        except Exception as exc:
            self._reset_explicit_motion_state(now=self._current_time())
            self._sync_head_tracking()
            return f"Error: move_head failed: {type(exc).__name__}: {exc}"

        return self._format_started_message(
            base_message=f"Moved head {normalized_direction}",
            claim=claim,
            requested_kind="move_head",
        )

    def play_emotion(self, emotion_name: str, library: Any) -> str:
        """Queue one recorded emotion move through the shared movement manager."""

        movement_manager = self.movement_manager
        if movement_manager is None:
            return "Error: play_emotion requires a connected ReachyMini runtime"
        claim = self._claim_explicit_motion(
            "emotion",
            self._resolve_move_duration(library, emotion_name),
        )
        if not claim.allowed:
            return self._format_deferred_message(
                action_label=f"emotion {emotion_name}",
                blocking_kind=claim.blocking_kind,
            )
        try:
            movement_manager.queue_move(EmotionQueueMove(emotion_name, library))
            if hasattr(movement_manager, "mark_activity"):
                movement_manager.mark_activity()
        except Exception as exc:
            self._reset_explicit_motion_state(now=self._current_time())
            self._sync_head_tracking()
            return f"Error: play_emotion failed: {type(exc).__name__}: {exc}"
        return self._format_started_message(
            base_message=f"Playing emotion {emotion_name}",
            claim=claim,
            requested_kind="emotion",
        )

    def dance(self, move_name: str, repeat: int, library: Any) -> str:
        """Queue one or more recorded dance moves through the shared movement manager."""

        movement_manager = self.movement_manager
        if movement_manager is None:
            return "Error: dance requires a connected ReachyMini runtime"

        repeat_count = max(1, int(repeat))
        claim = self._claim_explicit_motion(
            "dance",
            self._resolve_move_duration(library, move_name) * repeat_count,
        )
        if not claim.allowed:
            return self._format_deferred_message(
                action_label=f"dance {move_name} x{repeat_count}",
                blocking_kind=claim.blocking_kind,
            )
        try:
            for _ in range(repeat_count):
                movement_manager.queue_move(DanceQueueMove(move_name, library))
            if hasattr(movement_manager, "mark_activity"):
                movement_manager.mark_activity()
        except Exception as exc:
            self._reset_explicit_motion_state(now=self._current_time())
            self._sync_head_tracking()
            return f"Error: dance failed: {type(exc).__name__}: {exc}"
        return self._format_started_message(
            base_message=f"Playing dance {move_name} x{repeat_count}",
            claim=claim,
            requested_kind="dance",
        )

    def clear_motion_queue(self, *, label: str = "motion") -> str:
        """Clear queued expressive motion and return a human-readable status."""

        movement_manager = self.movement_manager
        if movement_manager is None or not hasattr(movement_manager, "clear_move_queue"):
            return f"Error: stop_{label} requires a connected ReachyMini runtime"

        movement_manager.clear_move_queue()
        self._reset_explicit_motion_state(now=self._current_time())
        self._sync_head_tracking()
        if label == "dance":
            return "Stopped dance and cleared queue"
        if label == "emotion":
            return "Stopped emotion and cleared queue"
        return "Cleared queued motion"

    def _current_time(self) -> float:
        return float(self.now_fn())

    def _refresh_public_state(self) -> None:
        """Refresh timed coordination state before exposing public snapshots."""

        now = self._current_time()
        self._refresh_explicit_motion_state(now)
        surface_driver = self.surface_driver
        if surface_driver is not None:
            self._current_surface_state = surface_driver.current_state
            self._current_phase = str(
                self._current_surface_state.get("phase", self._current_phase) or self._current_phase
            )
        self._sync_head_tracking(now)

    def _explicit_motion_active(self, now: float | None = None) -> bool:
        resolved_now = self._current_time() if now is None else float(now)
        self._refresh_explicit_motion_state(resolved_now)
        return resolved_now < self._explicit_motion_until

    def _claim_explicit_motion(self, kind: str, duration: float) -> ExplicitMotionClaim:
        now = self._current_time()
        self._refresh_explicit_motion_state(now)
        requested_priority = int(_EXPLICIT_MOTION_PRIORITIES.get(kind, 0))
        if self._explicit_motion_kind is not None:
            active_kind = self._explicit_motion_kind
            active_priority = int(self._explicit_motion_priority)
            if requested_priority < active_priority:
                return ExplicitMotionClaim(
                    allowed=False,
                    blocking_kind=active_kind,
                )
            movement_manager = self.movement_manager
            if movement_manager is None or not hasattr(movement_manager, "clear_move_queue"):
                return ExplicitMotionClaim(
                    allowed=False,
                    blocking_kind=active_kind,
                )
            movement_manager.clear_move_queue()
            interrupted_kind = active_kind
        else:
            interrupted_kind = None

        self._explicit_motion_kind = kind
        self._explicit_motion_priority = requested_priority
        self._explicit_motion_until = now + max(float(duration), 0.0)
        speech_driver = self.speech_driver
        if speech_driver is not None and speech_driver.speech_active:
            speech_driver.reset_speech_motion()
        self._sync_head_tracking(now)
        return ExplicitMotionClaim(
            allowed=True,
            interrupted_kind=interrupted_kind,
        )

    def _refresh_explicit_motion_state(self, now: float) -> None:
        if self._explicit_motion_kind is None:
            return
        if float(now) < self._explicit_motion_until:
            return
        self._reset_explicit_motion_state(now=now)

    def _reset_explicit_motion_state(self, *, now: float) -> None:
        _ = now
        self._explicit_motion_until = 0.0
        self._explicit_motion_kind = None
        self._explicit_motion_priority = 0

    def _sync_head_tracking(self, now: float | None = None) -> None:
        camera_worker = self.camera_worker
        if camera_worker is None or not hasattr(camera_worker, "set_head_tracking_enabled"):
            self._head_tracking_active = False
            return

        resolved_now = self._current_time() if now is None else float(now)
        should_enable = self._desired_head_tracking_enabled and not self._explicit_motion_active(
            resolved_now
        )
        if should_enable == self._head_tracking_active:
            return
        camera_worker.set_head_tracking_enabled(should_enable)
        self._head_tracking_active = should_enable

    def _resolve_move_duration(self, library: Any, move_name: str) -> float:
        try:
            move = library.get(move_name)
            duration = float(getattr(move, "duration", self.motion_duration_s))
        except Exception:
            duration = float(self.motion_duration_s)
        return max(duration, 0.0)

    @staticmethod
    def _format_deferred_message(action_label: str, blocking_kind: str | None) -> str:
        blocked_by = blocking_kind or "another motion"
        return f"Deferred {action_label} while {blocked_by} is active"

    @staticmethod
    def _format_started_message(
        *,
        base_message: str,
        claim: ExplicitMotionClaim,
        requested_kind: str,
    ) -> str:
        interrupted_kind = claim.interrupted_kind
        if not interrupted_kind:
            return base_message
        if interrupted_kind == requested_kind:
            return f"{base_message} (replaced {interrupted_kind})"
        return f"{base_message} (preempted {interrupted_kind})"

    @staticmethod
    def _normalize_phase(state: Mapping[str, Any] | None) -> str:
        if not isinstance(state, Mapping):
            return "idle"

        value = str(state.get("phase", "") or "").strip().lower()
        if value in {"idle", "settling", "listening_wait", "replying", "listening"}:
            return value
        return "idle"

    @classmethod
    def _normalize_surface_state(cls, state: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(state, Mapping):
            return {"phase": "idle"}
        payload = dict(state)
        payload["phase"] = cls._normalize_phase(state)
        return payload
