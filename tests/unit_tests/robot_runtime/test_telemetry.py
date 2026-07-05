"""Tests for RobotRuntime telemetry."""

from __future__ import annotations

import json

import pytest

from reachy_mini.robot_runtime.contracts import RobotEvent
from reachy_mini.robot_runtime.telemetry import RobotTelemetrySink, RobotTraceContext


def test_trace_context_records_events_with_ids() -> None:
    """Trace context carries ids into RobotEvent."""
    sink = RobotTelemetrySink(max_events=2)
    context = RobotTraceContext(turn_id="turn", intent_id="intent")

    sink.record_context(context, source="runtime", event_type="intent_received")
    sink.record_context(
        context.with_ids(plan_id="plan"),
        source="scheduler",
        event_type="plan_scheduled",
    )
    sink.record_context(context, source="runtime", event_type="extra")

    assert sink.counters() == {
        "intent_received": 1,
        "plan_scheduled": 1,
        "extra": 1,
    }
    assert [event.event_type for event in sink.events()] == ["plan_scheduled", "extra"]
    assert sink.snapshot()["count"] == 2


def test_trace_timeline_summarizes_complete_intent_chain() -> None:
    """A complete Runtime trace can be queried as a linked timeline."""
    sink = RobotTelemetrySink()
    for event in (
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            turn_id="turn",
            intent_id="intent",
        ),
        RobotEvent(
            source="scheduler",
            event_type="plan_scheduled",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
        ),
        RobotEvent(
            source="resolver",
            event_type="command_resolved",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
            command_id="command",
            payload={"adapter_id": "mujoco"},
        ),
        RobotEvent(
            source="safety",
            event_type="safety_decision",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
            command_id="command",
            status="allow",
        ),
        RobotEvent(
            source="runtime",
            event_type="command_started",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
            command_id="command",
            status="running",
            payload={"adapter_id": "mujoco"},
        ),
        RobotEvent(
            source="adapter",
            event_type="adapter_result",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
            command_id="command",
            status="completed",
            payload={"adapter_id": "mujoco"},
        ),
    ):
        sink.record(event)

    timeline = sink.trace_timeline(intent_id="intent")

    assert timeline.complete is True
    assert timeline.gaps == ()
    assert timeline.turn_ids == ("turn",)
    assert timeline.intent_ids == ("intent",)
    assert timeline.plan_ids == ("plan",)
    assert timeline.command_ids == ("command",)
    assert timeline.adapter_ids == ("mujoco",)
    assert timeline.event_types == (
        "intent_received",
        "plan_scheduled",
        "command_resolved",
        "safety_decision",
        "command_started",
        "adapter_result",
    )
    assert timeline.to_dict()["complete"] is True


def test_trace_timeline_requires_command_started_before_adapter_result() -> None:
    """Successful adapter traces expose a start event before the terminal result."""
    sink = RobotTelemetrySink()
    for event in (
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            turn_id="turn",
            intent_id="intent",
        ),
        RobotEvent(
            source="scheduler",
            event_type="plan_scheduled",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
        ),
        RobotEvent(
            source="resolver",
            event_type="command_resolved",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
            command_id="command",
        ),
        RobotEvent(
            source="safety",
            event_type="safety_decision",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
            command_id="command",
            status="allow",
        ),
        RobotEvent(
            source="adapter",
            event_type="adapter_result",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
            command_id="command",
            status="completed",
        ),
    ):
        sink.record(event)

    timeline = sink.trace_timeline(intent_id="intent")

    assert timeline.complete is False
    assert timeline.gaps == ("missing_command_started",)


def test_trace_timeline_treats_plan_rejected_as_terminal() -> None:
    """Plans rejected by the scheduler are complete terminal failure traces."""
    sink = RobotTelemetrySink()
    for event in (
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            turn_id="turn",
            intent_id="intent",
        ),
        RobotEvent(
            source="scheduler",
            event_type="plan_rejected",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
            status="failed",
            reason="plan cannot be scheduled after deadline_ms",
        ),
    ):
        sink.record(event)

    timeline = sink.trace_timeline(intent_id="intent")

    assert timeline.complete is True
    assert timeline.gaps == ()
    assert timeline.command_ids == ()
    assert timeline.event_types == ("intent_received", "plan_rejected")


def test_trace_timeline_treats_tool_rejected_as_terminal() -> None:
    """Tool validation failures are complete tool-layer traces."""
    sink = RobotTelemetrySink()
    sink.record(
        RobotEvent(
            source="tool",
            event_type="tool_rejected",
            turn_id="turn_tool",
            status="failed",
            payload={"tool": "emit_embodied_intent"},
        )
    )

    timeline = sink.trace_timeline(turn_id="turn_tool")

    assert timeline.complete is True
    assert timeline.gaps == ()
    assert timeline.turn_ids == ("turn_tool",)
    assert timeline.intent_ids == ()
    assert timeline.plan_ids == ()
    assert timeline.command_ids == ()
    assert timeline.event_types == ("tool_rejected",)


def test_trace_timeline_reports_missing_links() -> None:
    """Incomplete traces expose explicit gaps for observability gates."""
    sink = RobotTelemetrySink()
    sink.record(RobotEvent(source="runtime", event_type="intent_received", intent_id="i"))

    timeline = sink.trace_timeline(intent_id="i")

    assert timeline.complete is False
    assert "missing_turn_id" in timeline.gaps
    assert "missing_plan_id" in timeline.gaps
    assert "missing_terminal_event" in timeline.gaps


def test_trace_timeline_requires_identifier() -> None:
    """Trace queries must be scoped to avoid returning unrelated events."""
    sink = RobotTelemetrySink()

    with pytest.raises(ValueError, match="trace identifier"):
        sink.trace_timeline()


def test_metrics_snapshot_counts_intents_safety_adapters_and_latency() -> None:
    """Telemetry metrics summarize key Runtime observability signals."""
    sink = RobotTelemetrySink()
    for event in (
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            ts_ms=100,
            intent_id="i1",
        ),
        RobotEvent(
            source="safety",
            event_type="safety_decision",
            ts_ms=110,
            intent_id="i1",
            command_id="c1",
            status="degrade",
            payload={"reasons": ["timeout_limited", "intensity_limited"]},
        ),
        RobotEvent(
            source="adapter",
            event_type="adapter_result",
            ts_ms=130,
            intent_id="i1",
            command_id="c1",
            status="completed",
            payload={"adapter_id": "mujoco"},
        ),
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            ts_ms=200,
            intent_id="i2",
        ),
        RobotEvent(
            source="safety",
            event_type="safety_decision",
            ts_ms=205,
            intent_id="i2",
            command_id="c2",
            status="deny",
            payload={"reasons": ["runtime_not_active:inactive"]},
        ),
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            ts_ms=300,
            intent_id="i3",
        ),
        RobotEvent(
            source="adapter",
            event_type="adapter_result",
            ts_ms=350,
            intent_id="i3",
            command_id="c3",
            status="failed",
            payload={"adapter_id": "reachy", "error_code": "RuntimeError"},
        ),
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            ts_ms=400,
            intent_id="i4",
        ),
        RobotEvent(
            source="scheduler",
            event_type="plan_rejected",
            ts_ms=415,
            intent_id="i4",
            plan_id="p4",
            status="failed",
            reason="plan cannot be scheduled after deadline_ms",
            payload={
                "policy_name": "speech_sync_policy",
                "timing_anchor": "speech_start",
                "rejection_reason": "plan cannot be scheduled after deadline_ms",
            },
        ),
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            ts_ms=600,
            intent_id="i5",
        ),
        RobotEvent(
            source="adapter",
            event_type="adapter_result",
            ts_ms=620,
            intent_id="i5",
            command_id="c5",
            status="timeout",
            payload={"adapter_id": "mujoco", "error_code": "command_timeout"},
        ),
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            ts_ms=700,
            intent_id="i6",
        ),
        RobotEvent(
            source="resolver",
            event_type="capability_unresolved",
            ts_ms=708,
            intent_id="i6",
            plan_id="p6",
            status="unresolved",
            reason="no_capability_match",
            payload={
                "behavior_node": "RobotPolicyEngine/task_policy",
                "reasons": ["no_capability_match"],
            },
        ),
        RobotEvent(
            source="tool",
            event_type="tool_rejected",
            ts_ms=500,
            turn_id="turn_tool",
            status="failed",
            payload={"tool": "emit_embodied_intent", "error_type": "ValueError"},
        ),
        RobotEvent(
            source="tool",
            event_type="tool_rejected",
            ts_ms=510,
            turn_id="turn_tool_2",
            status="failed",
            payload={"tool": "query_robot_trace", "error_type": "ValueError"},
        ),
    ):
        sink.record(event)

    metrics = sink.metrics()

    assert metrics.event_count == 15
    assert metrics.intent_count == 6
    assert metrics.completed_intent_count == 6
    assert metrics.incomplete_intent_count == 0
    assert metrics.safety_denial_count == 1
    assert metrics.safety_degrade_count == 1
    assert metrics.safety_denials_by_reason == {"runtime_not_active:inactive": 1}
    assert metrics.safety_degrades_by_reason == {
        "timeout_limited": 1,
        "intensity_limited": 1,
    }
    assert metrics.adapter_success_count == 1
    assert metrics.adapter_failure_count == 2
    assert metrics.adapter_timeout_count == 1
    assert metrics.adapter_results_by_status == {
        "completed": 1,
        "failed": 1,
        "timeout": 1,
    }
    assert metrics.adapter_results_by_adapter == {"mujoco": 2, "reachy": 1}
    assert metrics.adapter_failures_by_adapter == {"mujoco": 1, "reachy": 1}
    assert metrics.adapter_failures_by_error_code == {
        "RuntimeError": 1,
        "command_timeout": 1,
    }
    assert metrics.adapter_timeouts_by_adapter == {"mujoco": 1}
    assert metrics.capability_unresolved_count == 1
    assert metrics.capability_unresolved_by_reason == {"no_capability_match": 1}
    assert metrics.capability_unresolved_by_behavior_node == {
        "RobotPolicyEngine/task_policy": 1
    }
    assert metrics.plan_rejection_count == 1
    assert metrics.plan_rejections_by_reason == {
        "plan cannot be scheduled after deadline_ms": 1
    }
    assert metrics.plan_rejections_by_policy == {"speech_sync_policy": 1}
    assert metrics.plan_rejections_by_timing_anchor == {"speech_start": 1}
    assert metrics.tool_rejection_count == 2
    assert metrics.tool_rejections_by_tool == {
        "emit_embodied_intent": 1,
        "query_robot_trace": 1,
    }
    assert metrics.tool_rejections_by_error_type == {"ValueError": 2}
    assert metrics.latency_ms_by_intent == {
        "i1": 30,
        "i2": 5,
        "i3": 50,
        "i4": 15,
        "i5": 20,
        "i6": 8,
    }
    assert metrics.average_latency_ms == 21.333
    assert metrics.max_latency_ms == 50
    assert sink.snapshot()["metrics"]["adapter_failure_count"] == 2
    assert sink.snapshot()["metrics"]["adapter_results_by_adapter"] == {
        "mujoco": 2,
        "reachy": 1,
    }
    assert sink.snapshot()["metrics"]["adapter_failures_by_error_code"] == {
        "RuntimeError": 1,
        "command_timeout": 1,
    }
    assert sink.snapshot()["metrics"]["safety_denials_by_reason"] == {
        "runtime_not_active:inactive": 1
    }
    assert sink.snapshot()["metrics"]["safety_degrades_by_reason"] == {
        "timeout_limited": 1,
        "intensity_limited": 1,
    }
    assert sink.snapshot()["metrics"]["plan_rejection_count"] == 1
    assert sink.snapshot()["metrics"]["plan_rejections_by_timing_anchor"] == {
        "speech_start": 1
    }
    assert sink.snapshot()["metrics"]["capability_unresolved_by_reason"] == {
        "no_capability_match": 1
    }
    assert sink.snapshot()["metrics"]["tool_rejection_count"] == 2
    assert sink.snapshot()["metrics"]["tool_rejections_by_tool"] == {
        "emit_embodied_intent": 1,
        "query_robot_trace": 1,
    }


def test_structured_log_lines_export_jsonl_records() -> None:
    """Telemetry events can be exported as stable JSONL records."""
    sink = RobotTelemetrySink()
    sink.record(
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            ts_ms=100,
            severity="info",
            turn_id="turn",
            intent_id="intent",
            payload={"intent_type": "greet"},
        )
    )
    sink.record(
        RobotEvent(
            source="adapter",
            event_type="adapter_result",
            ts_ms=120,
            severity="info",
            turn_id="turn",
            intent_id="intent",
            plan_id="plan",
            command_id="command",
            status="completed",
            payload={"adapter_id": "mujoco"},
        )
    )

    lines = sink.structured_log_lines(limit=1)
    payload = json.loads(lines[0])

    assert len(lines) == 1
    assert payload["schema_version"] == "robot_runtime.event.v1"
    assert payload["event_type"] == "adapter_result"
    assert payload["trace"] == {
        "turn_id": "turn",
        "intent_id": "intent",
        "plan_id": "plan",
        "command_id": "command",
    }
    assert payload["payload"] == {"adapter_id": "mujoco"}
    assert sink.structured_log_lines(limit=0) == []
