"""Pure counting helpers for RobotRuntime metrics."""

from __future__ import annotations

from reachy_mini.robot_runtime.contracts import RobotEvent


def latency_ms_by_intent(events: list[RobotEvent]) -> dict[str, int]:
    """Return latency from intent receipt to first terminal event."""
    starts: dict[str, int] = {}
    latencies: dict[str, int] = {}
    for event in events:
        if not event.intent_id:
            continue
        if event.event_type == "intent_received" and event.intent_id not in starts:
            starts[event.intent_id] = event.ts_ms
            continue
        if event.intent_id in latencies or event.intent_id not in starts:
            continue
        if is_terminal(event):
            latencies[event.intent_id] = max(0, event.ts_ms - starts[event.intent_id])
    return latencies


def is_terminal(event: RobotEvent) -> bool:
    """Return whether an event ends an intent trace for metric purposes."""
    if event.event_type in {
        "adapter_result",
        "adapter_missing",
        "capability_unresolved",
        "plan_rejected",
    }:
        return True
    return event.event_type == "safety_decision" and event.status == "deny"


def average(values: list[int]) -> float | None:
    """Return a stable rounded average."""
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def count_payload_value(
    events: list[RobotEvent],
    *,
    event_type: str,
    payload_key: str,
    statuses: set[str] | None = None,
) -> dict[str, int]:
    """Count non-empty payload values for matching event type/status."""
    counts: dict[str, int] = {}
    for event in events:
        if event.event_type != event_type:
            continue
        if statuses is not None and event.status not in statuses:
            continue
        value = event.payload.get(payload_key)
        _increment(counts, value)
    return counts


def count_event_status(
    events: list[RobotEvent],
    *,
    event_type: str,
) -> dict[str, int]:
    """Count statuses for one event type."""
    counts: dict[str, int] = {}
    for event in events:
        if event.event_type == event_type:
            _increment(counts, event.status)
    return counts


def count_payload_or_status(
    events: list[RobotEvent],
    *,
    event_type: str,
    payload_key: str,
) -> dict[str, int]:
    """Count payload values, falling back to event status."""
    counts: dict[str, int] = {}
    for event in events:
        if event.event_type == event_type:
            _increment(counts, event.payload.get(payload_key) or event.status)
    return counts


def count_payload_or_reason(
    events: list[RobotEvent],
    *,
    event_type: str,
    payload_key: str,
) -> dict[str, int]:
    """Count payload values, falling back to event reason."""
    counts: dict[str, int] = {}
    for event in events:
        if event.event_type == event_type:
            _increment(counts, event.payload.get(payload_key) or event.reason)
    return counts


def count_safety_reasons(events: list[RobotEvent], *, status: str) -> dict[str, int]:
    """Count safety reasons for a decision status."""
    return count_payload_reasons(
        events,
        event_type="safety_decision",
        status=status,
    )


def count_payload_reasons(
    events: list[RobotEvent],
    *,
    event_type: str,
    status: str | None = None,
) -> dict[str, int]:
    """Count each reason from a payload reasons list/string."""
    counts: dict[str, int] = {}
    for event in events:
        if event.event_type != event_type:
            continue
        if status is not None and event.status != status:
            continue
        for reason in payload_reasons(event):
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def payload_reasons(event: RobotEvent) -> tuple[str, ...]:
    """Return reasons from an event payload."""
    reasons = event.payload.get("reasons")
    if isinstance(reasons, list | tuple):
        return tuple(str(reason) for reason in reasons if str(reason))
    if isinstance(reasons, str) and reasons:
        return (reasons,)
    return ()


def _increment(counts: dict[str, int], value: object) -> None:
    if value is None:
        return
    text = str(value)
    if text:
        counts[text] = counts.get(text, 0) + 1


__all__ = [
    "average",
    "count_event_status",
    "count_payload_or_reason",
    "count_payload_or_status",
    "count_payload_reasons",
    "count_payload_value",
    "count_safety_reasons",
    "is_terminal",
    "latency_ms_by_intent",
    "payload_reasons",
]
