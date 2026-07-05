"""State store for RobotRuntime snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from reachy_mini.robot_runtime.contracts import (
    AdapterState,
    LifecycleState,
    RobotState,
    RuntimeMode,
    SafetyState,
    now_ms,
)


@dataclass(frozen=True, slots=True)
class RobotStateSnapshot:
    """Versioned state snapshot returned to callers and tests."""

    state: RobotState
    revision: int
    created_at_ms: int = field(default_factory=now_ms)


@dataclass(slots=True)
class RobotStateStore:
    """Small synchronous state store used by the async runtime loop."""

    _state: RobotState = field(default_factory=RobotState)
    _revision: int = 0

    def snapshot(self) -> RobotStateSnapshot:
        """Return the current immutable state snapshot."""
        return RobotStateSnapshot(state=self._state, revision=self._revision)

    def replace_state(self, state: RobotState) -> RobotStateSnapshot:
        """Replace the whole state and advance the revision."""
        self._state = state
        self._revision += 1
        return self.snapshot()

    def set_lifecycle(self, lifecycle: LifecycleState | str) -> RobotStateSnapshot:
        """Update lifecycle state."""
        return self.replace_state(
            replace(
                self._state,
                lifecycle=lifecycle,
                observed_at_ms=now_ms(),
            )
        )

    def set_mode(self, mode: RuntimeMode | str) -> RobotStateSnapshot:
        """Update runtime mode."""
        return self.replace_state(replace(self._state, mode=mode, observed_at_ms=now_ms()))

    def set_active_turn(self, turn_id: str | None) -> RobotStateSnapshot:
        """Update active turn id."""
        return self.replace_state(
            replace(self._state, active_turn_id=turn_id, observed_at_ms=now_ms())
        )

    def set_speech_state(self, speech_state: str) -> RobotStateSnapshot:
        """Update speech lifecycle state."""
        return self.replace_state(
            replace(self._state, speech_state=speech_state, observed_at_ms=now_ms())
        )

    def set_input_state(self, input_state: str) -> RobotStateSnapshot:
        """Update input lifecycle state."""
        return self.replace_state(
            replace(self._state, input_state=input_state, observed_at_ms=now_ms())
        )

    def set_adapter_state(self, adapter_state: AdapterState) -> RobotStateSnapshot:
        """Set one adapter state."""
        adapter_states = dict(self._state.adapter_states)
        adapter_states[adapter_state.adapter_id] = adapter_state
        return self.replace_state(
            replace(
                self._state,
                adapter_states=adapter_states,
                observed_at_ms=now_ms(),
            )
        )

    def set_safety_state(self, safety_state: SafetyState) -> RobotStateSnapshot:
        """Update aggregate safety state."""
        return self.replace_state(
            replace(self._state, safety_state=safety_state, observed_at_ms=now_ms())
        )

    def set_active_behaviors(self, behaviors: list[str]) -> RobotStateSnapshot:
        """Replace active behavior names."""
        return self.replace_state(
            replace(
                self._state,
                active_behaviors=list(behaviors),
                observed_at_ms=now_ms(),
            )
        )

    def set_active_commands(self, command_ids: list[str]) -> RobotStateSnapshot:
        """Replace active command ids."""
        return self.replace_state(
            replace(
                self._state,
                active_commands=list(command_ids),
                observed_at_ms=now_ms(),
            )
        )

    def set_capability_revision(self, revision: str) -> RobotStateSnapshot:
        """Update capability inventory revision."""
        return self.replace_state(
            replace(
                self._state,
                capability_revision=revision,
                observed_at_ms=now_ms(),
            )
        )


__all__ = ["RobotStateSnapshot", "RobotStateStore"]
