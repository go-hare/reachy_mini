"""Managed lifecycle support for RobotRuntime and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field

from reachy_mini.robot_runtime.contracts import LifecycleState, now_ms
from reachy_mini.robot_runtime.errors import LifecycleTransitionError

_ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.UNCONFIGURED: frozenset(
        {LifecycleState.CONFIGURING, LifecycleState.SHUTDOWN}
    ),
    LifecycleState.CONFIGURING: frozenset({LifecycleState.INACTIVE, LifecycleState.ERROR}),
    LifecycleState.INACTIVE: frozenset(
        {LifecycleState.CONFIGURING, LifecycleState.ACTIVATING, LifecycleState.SHUTDOWN}
    ),
    LifecycleState.ACTIVATING: frozenset({LifecycleState.ACTIVE, LifecycleState.ERROR}),
    LifecycleState.ACTIVE: frozenset({LifecycleState.DEACTIVATING, LifecycleState.ERROR}),
    LifecycleState.DEACTIVATING: frozenset({LifecycleState.INACTIVE, LifecycleState.ERROR}),
    LifecycleState.ERROR: frozenset({LifecycleState.RECOVERING, LifecycleState.SHUTDOWN}),
    LifecycleState.RECOVERING: frozenset({LifecycleState.INACTIVE, LifecycleState.ERROR}),
    LifecycleState.SHUTDOWN: frozenset(),
}


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    """One accepted lifecycle transition."""

    source: LifecycleState
    target: LifecycleState
    ts_ms: int = field(default_factory=now_ms)
    reason: str = ""


@dataclass(slots=True)
class LifecycleManager:
    """Validate and record managed lifecycle transitions."""

    state: LifecycleState = LifecycleState.UNCONFIGURED
    history: list[LifecycleTransition] = field(default_factory=list)

    def transition_to(
        self,
        target: LifecycleState | str,
        *,
        reason: str = "",
    ) -> LifecycleTransition:
        """Transition to a target state or raise if it is not allowed."""
        resolved = target if isinstance(target, LifecycleState) else LifecycleState(str(target))
        allowed = _ALLOWED_TRANSITIONS[self.state]
        if resolved not in allowed:
            raise LifecycleTransitionError(
                f"Cannot transition lifecycle from {self.state.value} to {resolved.value}"
            )
        transition = LifecycleTransition(source=self.state, target=resolved, reason=reason)
        self.state = resolved
        self.history.append(transition)
        return transition

    def configure(self, *, reason: str = "") -> LifecycleTransition:
        """Enter configuring from unconfigured or inactive."""
        return self.transition_to(LifecycleState.CONFIGURING, reason=reason)

    def mark_inactive(self, *, reason: str = "") -> LifecycleTransition:
        """Mark the runtime configured but inactive."""
        return self.transition_to(LifecycleState.INACTIVE, reason=reason)

    def activate(self, *, reason: str = "") -> LifecycleTransition:
        """Enter activating from inactive."""
        return self.transition_to(LifecycleState.ACTIVATING, reason=reason)

    def mark_active(self, *, reason: str = "") -> LifecycleTransition:
        """Mark the runtime active after activation work has completed."""
        return self.transition_to(LifecycleState.ACTIVE, reason=reason)

    def deactivate(self, *, reason: str = "") -> LifecycleTransition:
        """Enter deactivating from active."""
        return self.transition_to(LifecycleState.DEACTIVATING, reason=reason)

    def mark_error(self, *, reason: str = "") -> LifecycleTransition:
        """Move into error from an operational transition state."""
        return self.transition_to(LifecycleState.ERROR, reason=reason)

    def recover(self, *, reason: str = "") -> LifecycleTransition:
        """Begin recovery from error."""
        return self.transition_to(LifecycleState.RECOVERING, reason=reason)

    def shutdown(self, *, reason: str = "") -> LifecycleTransition:
        """Shutdown from a state that supports shutdown."""
        return self.transition_to(LifecycleState.SHUTDOWN, reason=reason)


__all__ = ["LifecycleManager", "LifecycleTransition"]
