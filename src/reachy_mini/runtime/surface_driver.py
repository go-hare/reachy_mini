"""Thin driver that maps runtime surface state onto body-level baseline signals."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Mapping

from reachy_mini.runtime.moves import MovementManager

_PHASE_PRIORITY: dict[str, int] = {
    "idle": 0,
    "settling": 1,
    "listening_wait": 2,
    "replying": 3,
    "listening": 4,
}


@dataclass(slots=True)
class _ThreadSurfaceEntry:
    """One normalized thread-local surface-state record."""

    phase: str
    state: dict[str, Any]
    order: int
    hold_until: float = 0.0
    idle_requested: bool = False


@dataclass(slots=True)
class SurfaceDriver:
    """Apply front/runtime lifecycle phases onto a shared movement manager."""

    movement_manager: MovementManager | None = None
    now_fn: Callable[[], float] = time.monotonic
    _thread_entries: dict[str, _ThreadSurfaceEntry] = field(default_factory=dict, init=False, repr=False)
    _current_phase: str = field(default="idle", init=False, repr=False)
    _current_state: dict[str, Any] = field(
        default_factory=lambda: {"phase": "idle"},
        init=False,
        repr=False,
    )
    _state_order: int = field(default=0, init=False, repr=False)

    @property
    def current_phase(self) -> str:
        """Return the currently applied aggregate surface phase."""

        self._refresh_entries()
        return self._current_phase

    @property
    def current_state(self) -> dict[str, Any]:
        """Return the latest aggregate surface-state snapshot."""

        self._refresh_entries()
        return dict(self._current_state)

    def apply_state(self, state: Mapping[str, Any] | None) -> str:
        """Apply one runtime surface-state snapshot and return the aggregate phase."""

        resolved_now = self._current_time()
        self._refresh_entries(resolved_now)
        normalized_state = self._normalize_state_payload(state)
        phase = str(normalized_state.get("phase", "idle") or "idle")
        thread_id = self._extract_thread_id(state)

        if thread_id:
            self._apply_thread_state(
                thread_id=thread_id,
                state=normalized_state,
                now=resolved_now,
            )
            aggregate_state = self._resolve_aggregate_state()
        else:
            aggregate_state = normalized_state

        self._set_current_state(aggregate_state)
        self._apply_phase(self._current_phase)
        return self._current_phase

    def _apply_phase(self, phase: str) -> None:
        movement_manager = self.movement_manager
        if movement_manager is None:
            return

        movement_manager.set_listening(phase == "listening")
        if phase != "idle":
            movement_manager.mark_activity()

    def _apply_thread_state(
        self,
        *,
        thread_id: str,
        state: dict[str, Any],
        now: float,
    ) -> None:
        phase = str(state.get("phase", "idle") or "idle")
        previous = self._thread_entries.get(thread_id)

        if phase == "idle":
            if previous is not None and previous.hold_until > now:
                previous.idle_requested = True
            else:
                self._thread_entries.pop(thread_id, None)
            return

        self._state_order += 1
        hold_ms = max(float(state.get("recommended_hold_ms", 0) or 0), 0.0)
        self._thread_entries[thread_id] = _ThreadSurfaceEntry(
            phase=phase,
            state=dict(state),
            order=self._state_order,
            hold_until=now + (hold_ms / 1000.0),
            idle_requested=False,
        )

    def _refresh_entries(self, now: float | None = None) -> None:
        resolved_now = self._current_time() if now is None else float(now)
        stale_thread_ids = [
            thread_id
            for thread_id, entry in self._thread_entries.items()
            if entry.idle_requested and entry.hold_until <= resolved_now
        ]
        for thread_id in stale_thread_ids:
            self._thread_entries.pop(thread_id, None)
        self._set_current_state(self._resolve_aggregate_state())

    def _resolve_aggregate_state(self) -> dict[str, Any]:
        if not self._thread_entries:
            return {"phase": "idle"}
        selected = max(
            self._thread_entries.values(),
            key=lambda entry: (_PHASE_PRIORITY.get(entry.phase, 0), entry.order),
        )
        return dict(selected.state)

    def _set_current_state(self, state: Mapping[str, Any] | None) -> None:
        normalized_state = self._normalize_state_payload(state)
        self._current_state = normalized_state
        self._current_phase = str(normalized_state.get("phase", "idle") or "idle")

    def _current_time(self) -> float:
        return float(self.now_fn())

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

    @classmethod
    def _normalize_state_payload(cls, state: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(state, Mapping):
            return {"phase": "idle"}
        payload = dict(state)
        payload["phase"] = cls._normalize_phase(state)
        return payload

