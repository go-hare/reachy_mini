"""Policy service request/response contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from reachy_mini.robot_runtime.contracts import EmbodiedIntent, RobotState


@dataclass(frozen=True, slots=True)
class PolicyServiceRequest:
    """Request sent to an external embodied policy service."""

    state: RobotState
    intent: EmbodiedIntent
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize request into JSON-friendly data."""
        return {
            "state": self.state.to_dict(),
            "intent": self.intent.to_dict(),
            "context": dict(self.context),
        }


@dataclass(frozen=True, slots=True)
class PolicyServiceResponse:
    """Response returned by an external embodied policy service."""

    status: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize response into JSON-friendly data."""
        return {
            "status": self.status,
            "actions": [dict(action) for action in self.actions],
            "reason": self.reason,
        }

    @classmethod
    def safe_idle(cls, reason: str) -> "PolicyServiceResponse":
        """Return a safe fallback response."""
        return cls(status="safe_idle", actions=[], reason=reason)


class PolicyServiceBackend(Protocol):
    """Sync policy backend protocol used by RobotRuntime policy adapters."""

    def plan(self, request: PolicyServiceRequest) -> PolicyServiceResponse:
        """Return a policy response for one embodied intent and state."""


__all__ = [
    "PolicyServiceBackend",
    "PolicyServiceRequest",
    "PolicyServiceResponse",
]
