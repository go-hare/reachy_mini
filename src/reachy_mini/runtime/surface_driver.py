"""Thin driver that maps runtime surface state onto body-level baseline signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from reachy_mini.runtime.moves import MovementManager

_PHASE_PRIORITY: dict[str, int] = {
    "idle": 0,
    "settling": 1,
    "replying": 2,
    "listening": 3,
}


@dataclass(slots=True)
class SurfaceDriver:
    """Apply front/runtime lifecycle phases onto a shared movement manager."""

    movement_manager: MovementManager | None = None
    _thread_phases: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _current_phase: str = field(default="idle", init=False, repr=False)

    @property
    def current_phase(self) -> str:
        """Return the currently applied aggregate surface phase."""

        return self._current_phase

    def apply_state(self, state: Mapping[str, Any] | None) -> str:
        """Apply one runtime surface-state snapshot and return the aggregate phase."""

        phase = self._normalize_phase(state)
        thread_id = self._extract_thread_id(state)

        if thread_id:
            if phase == "idle":
                self._thread_phases.pop(thread_id, None)
            else:
                self._thread_phases[thread_id] = phase
            aggregate_phase = self._resolve_aggregate_phase()
        else:
            aggregate_phase = phase

        self._current_phase = aggregate_phase
        self._apply_phase(aggregate_phase)
        return aggregate_phase

    def _apply_phase(self, phase: str) -> None:
        movement_manager = self.movement_manager
        if movement_manager is None:
            return

        movement_manager.set_listening(phase == "listening")
        if phase != "idle":
            movement_manager.mark_activity()

    def _resolve_aggregate_phase(self) -> str:
        if not self._thread_phases:
            return "idle"
        return max(
            self._thread_phases.values(),
            key=lambda phase: _PHASE_PRIORITY.get(phase, 0),
        )

    @staticmethod
    def _extract_thread_id(state: Mapping[str, Any] | None) -> str:
        if not isinstance(state, Mapping):
            return ""
        return str(state.get("thread_id", "") or "").strip()

    @staticmethod
    def _normalize_phase(state: Mapping[str, Any] | None) -> str:
        if not isinstance(state, Mapping):
            return "idle"

        for key in ("phase", "lifecycle_phase", "speaking_phase"):
            value = str(state.get(key, "") or "").strip().lower()
            if value in _PHASE_PRIORITY:
                return value
        return "idle"

