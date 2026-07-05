"""Telemetry sink and trace helpers for RobotRuntime."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from reachy_mini.robot_runtime.contracts import RobotEvent
from reachy_mini.robot_runtime.metrics import RobotMetricsSnapshot
from reachy_mini.robot_runtime.structured_log import (
    RobotStructuredLogRecord,
    structured_log_lines,
    structured_log_records,
)


@dataclass(frozen=True, slots=True)
class RobotTraceContext:
    """Trace identity carried across intent, plan, command, and adapter results."""

    turn_id: str | None = None
    intent_id: str | None = None
    plan_id: str | None = None
    command_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def event(
        self,
        *,
        source: str,
        event_type: str,
        severity: str = "info",
        status: str | None = None,
        payload: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> RobotEvent:
        """Create a RobotEvent populated from this trace context."""
        return RobotEvent(
            source=source,
            event_type=event_type,
            severity=severity,
            turn_id=self.turn_id,
            intent_id=self.intent_id,
            plan_id=self.plan_id,
            command_id=self.command_id,
            status=status,
            payload={**self.metadata, **dict(payload or {})},
            reason=reason,
        )

    def with_ids(self, **ids: str | None) -> "RobotTraceContext":
        """Return a copy with updated trace ids."""
        return replace(self, **ids)


@dataclass(slots=True)
class RobotTelemetrySink:
    """Bounded in-memory telemetry sink used by tests and local runtime."""

    max_events: int = 1000
    _events: list[RobotEvent] = field(default_factory=list)
    _counters: dict[str, int] = field(default_factory=dict)

    def record(self, event: RobotEvent) -> RobotEvent:
        """Record one event and update event type counters."""
        self._events.append(event)
        if len(self._events) > self.max_events:
            self._events = self._events[-self.max_events :]
        self._counters[event.event_type] = self._counters.get(event.event_type, 0) + 1
        return event

    def record_context(
        self,
        context: RobotTraceContext,
        *,
        source: str,
        event_type: str,
        severity: str = "info",
        status: str | None = None,
        payload: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> RobotEvent:
        """Create and record an event from a trace context."""
        return self.record(
            context.event(
                source=source,
                event_type=event_type,
                severity=severity,
                status=status,
                payload=payload,
                reason=reason,
            )
        )

    def events(self) -> list[RobotEvent]:
        """Return a copy of recorded events."""
        return list(self._events)

    def trace_timeline(
        self,
        *,
        turn_id: str | None = None,
        intent_id: str | None = None,
        plan_id: str | None = None,
        command_id: str | None = None,
    ) -> "RobotTraceTimeline":
        """Return a filtered timeline with trace completeness metadata."""
        if not any((turn_id, intent_id, plan_id, command_id)):
            raise ValueError("trace_timeline requires at least one trace identifier")
        events = tuple(
            event
            for event in self._events
            if _matches_trace(
                event,
                turn_id=turn_id,
                intent_id=intent_id,
                plan_id=plan_id,
                command_id=command_id,
            )
        )
        return RobotTraceTimeline.from_events(events)

    def counters(self) -> dict[str, int]:
        """Return event counters."""
        return dict(self._counters)

    def metrics(self) -> RobotMetricsSnapshot:
        """Return derived observability metrics."""
        return RobotMetricsSnapshot.from_events(self.events())

    def structured_log_records(
        self,
        *,
        limit: int | None = None,
    ) -> list[RobotStructuredLogRecord]:
        """Return stable JSONL record objects for telemetry events."""
        return structured_log_records(self.events(), limit=limit)

    def structured_log_lines(self, *, limit: int | None = None) -> list[str]:
        """Return telemetry events as structured JSON lines."""
        return structured_log_lines(self.events(), limit=limit)

    def snapshot(self) -> dict[str, Any]:
        """Return JSON-friendly telemetry state."""
        return {
            "count": len(self._events),
            "counters": self.counters(),
            "metrics": self.metrics().to_dict(),
            "events": [event.to_dict() for event in self._events],
        }


@dataclass(frozen=True, slots=True)
class RobotTraceTimeline:
    """Trace view joining intent, plan, command, safety, and adapter events."""

    events: tuple[RobotEvent, ...]
    turn_ids: tuple[str, ...]
    intent_ids: tuple[str, ...]
    plan_ids: tuple[str, ...]
    command_ids: tuple[str, ...]
    adapter_ids: tuple[str, ...]
    event_types: tuple[str, ...]
    complete: bool
    gaps: tuple[str, ...] = ()

    @classmethod
    def from_events(cls, events: tuple[RobotEvent, ...]) -> "RobotTraceTimeline":
        """Build a timeline from already-filtered events."""
        event_types = tuple(event.event_type for event in events)
        gaps = _trace_gaps(events, event_types)
        return cls(
            events=events,
            turn_ids=_unique_ids(event.turn_id for event in events),
            intent_ids=_unique_ids(event.intent_id for event in events),
            plan_ids=_unique_ids(event.plan_id for event in events),
            command_ids=_unique_ids(event.command_id for event in events),
            adapter_ids=_unique_ids(_adapter_id(event) for event in events),
            event_types=event_types,
            complete=not gaps,
            gaps=gaps,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the timeline for tools, tests, and websocket consumers."""
        return {
            "complete": self.complete,
            "gaps": list(self.gaps),
            "turn_ids": list(self.turn_ids),
            "intent_ids": list(self.intent_ids),
            "plan_ids": list(self.plan_ids),
            "command_ids": list(self.command_ids),
            "adapter_ids": list(self.adapter_ids),
            "event_types": list(self.event_types),
            "events": [event.to_dict() for event in self.events],
        }


def _matches_trace(
    event: RobotEvent,
    *,
    turn_id: str | None,
    intent_id: str | None,
    plan_id: str | None,
    command_id: str | None,
) -> bool:
    if turn_id is not None and event.turn_id != turn_id:
        return False
    if intent_id is not None and event.intent_id != intent_id:
        return False
    if plan_id is not None and event.plan_id != plan_id:
        return False
    if command_id is not None and event.command_id != command_id:
        return False
    return True


def _unique_ids(values: Iterable[object]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _adapter_id(event: RobotEvent) -> str | None:
    value = event.payload.get("adapter_id")
    return str(value) if value is not None else None


def _trace_gaps(
    events: tuple[RobotEvent, ...],
    event_types: tuple[str, ...],
) -> tuple[str, ...]:
    gaps: list[str] = []
    if not events:
        return ("no_events",)
    event_type_set = set(event_types)
    if not _unique_ids(event.turn_id for event in events):
        gaps.append("missing_turn_id")
    if _requires_intent_id(event_type_set) and not _unique_ids(
        event.intent_id for event in events
    ):
        gaps.append("missing_intent_id")
    if _requires_plan_id(event_type_set) and not _unique_ids(
        event.plan_id for event in events
    ):
        gaps.append("missing_plan_id")
    if _requires_command_id(event_type_set) and not _unique_ids(
        event.command_id for event in events
    ):
        gaps.append("missing_command_id")
    for required in _required_event_types(events, event_type_set):
        if required not in event_types:
            gaps.append(f"missing_{required}")
    if not _has_terminal_event(events):
        gaps.append("missing_terminal_event")
    return tuple(gaps)


def _requires_command_id(event_types: set[str]) -> bool:
    return bool(event_types & {"adapter_result", "adapter_missing", "safety_decision"})


def _requires_intent_id(event_types: set[str]) -> bool:
    return not _is_tool_rejection_trace(event_types)


def _requires_plan_id(event_types: set[str]) -> bool:
    return not _is_tool_rejection_trace(event_types)


def _required_event_types(
    events: tuple[RobotEvent, ...],
    event_types: set[str],
) -> tuple[str, ...]:
    if _is_tool_rejection_trace(event_types):
        return ("tool_rejected",)
    if "plan_rejected" in event_types:
        return ("intent_received", "plan_rejected")
    if "capability_unresolved" in event_types:
        return ("intent_received", "plan_scheduled", "capability_unresolved")
    if "adapter_missing" in event_types:
        return (
            "intent_received",
            "plan_scheduled",
            "command_resolved",
            "safety_decision",
            "adapter_missing",
        )
    if "adapter_result" in event_types:
        return (
            "intent_received",
            "plan_scheduled",
            "command_resolved",
            "safety_decision",
            "command_started",
            "adapter_result",
        )
    if _has_safety_denial(events):
        return (
            "intent_received",
            "plan_scheduled",
            "command_resolved",
            "safety_decision",
        )
    return ("intent_received",)


def _has_terminal_event(events: tuple[RobotEvent, ...]) -> bool:
    terminal_types = {
        "adapter_result",
        "adapter_missing",
        "capability_unresolved",
        "plan_rejected",
        "tool_rejected",
    }
    for event in events:
        if event.event_type in terminal_types:
            return True
    return _has_safety_denial(events)


def _is_tool_rejection_trace(event_types: set[str]) -> bool:
    return "tool_rejected" in event_types and not bool(
        event_types
        & {
            "intent_received",
            "plan_scheduled",
            "command_resolved",
            "safety_decision",
            "command_started",
            "adapter_result",
            "adapter_missing",
            "capability_unresolved",
            "plan_rejected",
        }
    )


def _has_safety_denial(events: tuple[RobotEvent, ...]) -> bool:
    for event in events:
        if event.event_type == "safety_decision" and event.status == "deny":
            return True
    return False


__all__ = ["RobotTelemetrySink", "RobotTraceContext", "RobotTraceTimeline"]
