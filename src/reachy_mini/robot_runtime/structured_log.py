"""Structured JSONL log records for RobotRuntime events."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from reachy_mini.robot_runtime.contracts import RobotEvent

SCHEMA_VERSION = "robot_runtime.event.v1"


@dataclass(frozen=True, slots=True)
class RobotStructuredLogRecord:
    """Stable JSONL representation of one RobotEvent."""

    event: RobotEvent
    schema_version: str = SCHEMA_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize one event into an audit-friendly JSON object."""
        return {
            "schema_version": self.schema_version,
            "ts_ms": self.event.ts_ms,
            "event_id": self.event.event_id,
            "source": self.event.source,
            "event_type": self.event.event_type,
            "severity": self.event.severity,
            "status": self.event.status,
            "reason": self.event.reason,
            "trace": {
                "turn_id": self.event.turn_id,
                "intent_id": self.event.intent_id,
                "plan_id": self.event.plan_id,
                "command_id": self.event.command_id,
            },
            "payload": dict(self.event.payload),
            **self.extra,
        }

    def to_json_line(self) -> str:
        """Return this record as one JSON line."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def structured_log_records(
    events: list[RobotEvent],
    *,
    limit: int | None = None,
) -> list[RobotStructuredLogRecord]:
    """Convert events to structured records, optionally taking the latest N."""
    if limit is None:
        selected = events
    elif limit <= 0:
        selected = []
    else:
        selected = events[-limit:]
    return [RobotStructuredLogRecord(event=event) for event in selected]


def structured_log_lines(
    events: list[RobotEvent],
    *,
    limit: int | None = None,
) -> list[str]:
    """Convert events to JSONL strings, optionally taking the latest N."""
    return [record.to_json_line() for record in structured_log_records(events, limit=limit)]


__all__ = [
    "RobotStructuredLogRecord",
    "SCHEMA_VERSION",
    "structured_log_lines",
    "structured_log_records",
]
