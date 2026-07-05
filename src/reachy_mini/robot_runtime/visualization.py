"""Optional visualization export helpers for RobotRuntime telemetry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from reachy_mini.robot_runtime.contracts import RobotEvent
from reachy_mini.robot_runtime.telemetry import RobotTraceTimeline


@dataclass(frozen=True, slots=True)
class RobotVisualizationRecord:
    """Serializable visualization record derived from one RobotEvent."""

    path: str
    ts_ms: int
    event_type: str
    source: str
    severity: str
    status: str | None
    trace: dict[str, str | None]
    payload: dict[str, Any]
    reason: str | None = None

    @classmethod
    def from_event(
        cls,
        event: RobotEvent,
        *,
        index: int,
        entity_prefix: str,
    ) -> "RobotVisualizationRecord":
        """Create a visualization record from a Runtime event."""
        return cls(
            path=f"{entity_prefix}/{index:04d}_{event.source}_{event.event_type}",
            ts_ms=event.ts_ms,
            event_type=event.event_type,
            source=event.source,
            severity=event.severity,
            status=event.status,
            trace={
                "turn_id": event.turn_id,
                "intent_id": event.intent_id,
                "plan_id": event.plan_id,
                "command_id": event.command_id,
            },
            payload=dict(event.payload),
            reason=event.reason,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this visualization record."""
        return {
            "path": self.path,
            "ts_ms": self.ts_ms,
            "event_type": self.event_type,
            "source": self.source,
            "severity": self.severity,
            "status": self.status,
            "trace": dict(self.trace),
            "payload": dict(self.payload),
            "reason": self.reason,
        }

    def to_text(self) -> str:
        """Return a compact text payload suitable for Rerun TextLog."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class RerunExportResult:
    """Summary of a visualization export."""

    record_count: int
    paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialize export metadata."""
        return {"record_count": self.record_count, "paths": list(self.paths)}


@dataclass(frozen=True, slots=True)
class RerunTimelineExporter:
    """Export trace timelines to a Rerun-compatible recorder."""

    entity_prefix: str = "robot_runtime"

    def records(self, timeline: RobotTraceTimeline) -> list[RobotVisualizationRecord]:
        """Convert one trace timeline to ordered visualization records."""
        return [
            RobotVisualizationRecord.from_event(
                event,
                index=index,
                entity_prefix=self.entity_prefix,
            )
            for index, event in enumerate(timeline.events, start=1)
        ]

    def export(
        self,
        timeline: RobotTraceTimeline,
        *,
        recorder: Any | None = None,
    ) -> RerunExportResult:
        """Export a timeline using an injected or installed Rerun recorder."""
        rr = recorder if recorder is not None else _load_rerun()
        records = self.records(timeline)
        for record in records:
            _set_time(rr, record.ts_ms)
            _log_record(rr, record)
        return RerunExportResult(
            record_count=len(records),
            paths=tuple(record.path for record in records),
        )


def visualization_records(
    timeline: RobotTraceTimeline,
    *,
    entity_prefix: str = "robot_runtime",
) -> list[RobotVisualizationRecord]:
    """Return serializable visualization records for a trace timeline."""
    return RerunTimelineExporter(entity_prefix=entity_prefix).records(timeline)


def export_timeline_to_rerun(
    timeline: RobotTraceTimeline,
    *,
    recorder: Any | None = None,
    entity_prefix: str = "robot_runtime",
) -> RerunExportResult:
    """Export a trace timeline to Rerun or a compatible recorder."""
    return RerunTimelineExporter(entity_prefix=entity_prefix).export(
        timeline,
        recorder=recorder,
    )


def _load_rerun() -> Any:
    try:
        import rerun as rr
    except ImportError as exc:  # pragma: no cover - optional dependency guard
        raise RuntimeError("rerun-sdk is required for RobotRuntime Rerun export") from exc
    return rr


def _set_time(recorder: Any, ts_ms: int) -> None:
    if hasattr(recorder, "set_time_sequence"):
        recorder.set_time_sequence("robot_time_ms", ts_ms)
        return
    if hasattr(recorder, "set_time_seconds"):
        recorder.set_time_seconds("robot_time", ts_ms / 1000)


def _log_record(recorder: Any, record: RobotVisualizationRecord) -> None:
    if not hasattr(recorder, "log"):
        raise TypeError("recorder must provide a log(path, payload) method")
    recorder.log(record.path, _text_payload(recorder, record))


def _text_payload(recorder: Any, record: RobotVisualizationRecord) -> Any:
    factory = getattr(recorder, "TextLog", None)
    if factory is None:
        return record.to_text()
    return factory(record.to_text(), level=record.severity.upper())


__all__ = [
    "RerunExportResult",
    "RerunTimelineExporter",
    "RobotVisualizationRecord",
    "export_timeline_to_rerun",
    "visualization_records",
]
