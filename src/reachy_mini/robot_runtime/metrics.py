"""Derived metrics for RobotRuntime telemetry events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reachy_mini.robot_runtime.contracts import RobotEvent
from reachy_mini.robot_runtime.metrics_counts import (
    average,
    count_event_status,
    count_payload_or_reason,
    count_payload_or_status,
    count_payload_reasons,
    count_payload_value,
    count_safety_reasons,
    latency_ms_by_intent,
)


@dataclass(frozen=True, slots=True)
class RobotMetricsSnapshot:
    """JSON-friendly observability metrics derived from RobotEvent history."""

    event_count: int = 0
    intent_count: int = 0
    completed_intent_count: int = 0
    incomplete_intent_count: int = 0
    safety_denial_count: int = 0
    safety_degrade_count: int = 0
    safety_denials_by_reason: dict[str, int] = field(default_factory=dict)
    safety_degrades_by_reason: dict[str, int] = field(default_factory=dict)
    adapter_success_count: int = 0
    adapter_failure_count: int = 0
    adapter_timeout_count: int = 0
    adapter_results_by_status: dict[str, int] = field(default_factory=dict)
    adapter_results_by_adapter: dict[str, int] = field(default_factory=dict)
    adapter_failures_by_adapter: dict[str, int] = field(default_factory=dict)
    adapter_failures_by_error_code: dict[str, int] = field(default_factory=dict)
    adapter_timeouts_by_adapter: dict[str, int] = field(default_factory=dict)
    adapter_missing_count: int = 0
    adapter_missing_by_adapter: dict[str, int] = field(default_factory=dict)
    capability_unresolved_count: int = 0
    capability_unresolved_by_reason: dict[str, int] = field(default_factory=dict)
    capability_unresolved_by_behavior_node: dict[str, int] = field(default_factory=dict)
    plan_rejection_count: int = 0
    plan_rejections_by_reason: dict[str, int] = field(default_factory=dict)
    plan_rejections_by_policy: dict[str, int] = field(default_factory=dict)
    plan_rejections_by_timing_anchor: dict[str, int] = field(default_factory=dict)
    tool_rejection_count: int = 0
    tool_rejections_by_tool: dict[str, int] = field(default_factory=dict)
    tool_rejections_by_error_type: dict[str, int] = field(default_factory=dict)
    latency_ms_by_intent: dict[str, int] = field(default_factory=dict)
    average_latency_ms: float | None = None
    max_latency_ms: int | None = None

    @classmethod
    def from_events(cls, events: list[RobotEvent]) -> "RobotMetricsSnapshot":
        """Derive metrics from ordered telemetry events."""
        latency_by_intent = latency_ms_by_intent(events)
        intent_count = sum(1 for event in events if event.event_type == "intent_received")
        completed_intent_count = len(latency_by_intent)
        incomplete_intent_count = max(0, intent_count - completed_intent_count)
        latencies = list(latency_by_intent.values())
        return cls(
            event_count=len(events),
            intent_count=intent_count,
            completed_intent_count=completed_intent_count,
            incomplete_intent_count=incomplete_intent_count,
            safety_denial_count=sum(
                1
                for event in events
                if event.event_type == "safety_decision" and event.status == "deny"
            ),
            safety_degrade_count=sum(
                1
                for event in events
                if event.event_type == "safety_decision" and event.status == "degrade"
            ),
            safety_denials_by_reason=count_safety_reasons(events, status="deny"),
            safety_degrades_by_reason=count_safety_reasons(events, status="degrade"),
            adapter_success_count=sum(
                1
                for event in events
                if event.event_type == "adapter_result" and event.status == "completed"
            ),
            adapter_failure_count=sum(
                1
                for event in events
                if event.event_type == "adapter_result"
                and event.status in {"failed", "timeout"}
            ),
            adapter_timeout_count=sum(
                1
                for event in events
                if event.event_type == "adapter_result" and event.status == "timeout"
            ),
            adapter_results_by_status=count_event_status(
                events,
                event_type="adapter_result",
            ),
            adapter_results_by_adapter=count_payload_value(
                events,
                event_type="adapter_result",
                payload_key="adapter_id",
            ),
            adapter_failures_by_adapter=count_payload_value(
                events,
                event_type="adapter_result",
                payload_key="adapter_id",
                statuses={"failed", "timeout"},
            ),
            adapter_failures_by_error_code=count_payload_value(
                events,
                event_type="adapter_result",
                payload_key="error_code",
                statuses={"failed", "timeout"},
            ),
            adapter_timeouts_by_adapter=count_payload_value(
                events,
                event_type="adapter_result",
                payload_key="adapter_id",
                statuses={"timeout"},
            ),
            adapter_missing_count=sum(
                1 for event in events if event.event_type == "adapter_missing"
            ),
            adapter_missing_by_adapter=count_payload_or_status(
                events,
                event_type="adapter_missing",
                payload_key="adapter_id",
            ),
            capability_unresolved_count=sum(
                1 for event in events if event.event_type == "capability_unresolved"
            ),
            capability_unresolved_by_reason=count_payload_reasons(
                events,
                event_type="capability_unresolved",
            ),
            capability_unresolved_by_behavior_node=count_payload_value(
                events,
                event_type="capability_unresolved",
                payload_key="behavior_node",
            ),
            plan_rejection_count=sum(
                1 for event in events if event.event_type == "plan_rejected"
            ),
            plan_rejections_by_reason=count_payload_or_reason(
                events,
                event_type="plan_rejected",
                payload_key="rejection_reason",
            ),
            plan_rejections_by_policy=count_payload_value(
                events,
                event_type="plan_rejected",
                payload_key="policy_name",
            ),
            plan_rejections_by_timing_anchor=count_payload_value(
                events,
                event_type="plan_rejected",
                payload_key="timing_anchor",
            ),
            tool_rejection_count=sum(
                1 for event in events if event.event_type == "tool_rejected"
            ),
            tool_rejections_by_tool=count_payload_value(
                events,
                event_type="tool_rejected",
                payload_key="tool",
            ),
            tool_rejections_by_error_type=count_payload_value(
                events,
                event_type="tool_rejected",
                payload_key="error_type",
            ),
            latency_ms_by_intent=latency_by_intent,
            average_latency_ms=average(latencies),
            max_latency_ms=max(latencies) if latencies else None,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize metrics into stable primitives."""
        return {
            "event_count": self.event_count,
            "intent_count": self.intent_count,
            "completed_intent_count": self.completed_intent_count,
            "incomplete_intent_count": self.incomplete_intent_count,
            "safety_denial_count": self.safety_denial_count,
            "safety_degrade_count": self.safety_degrade_count,
            "safety_denials_by_reason": dict(self.safety_denials_by_reason),
            "safety_degrades_by_reason": dict(self.safety_degrades_by_reason),
            "adapter_success_count": self.adapter_success_count,
            "adapter_failure_count": self.adapter_failure_count,
            "adapter_timeout_count": self.adapter_timeout_count,
            "adapter_results_by_status": dict(self.adapter_results_by_status),
            "adapter_results_by_adapter": dict(self.adapter_results_by_adapter),
            "adapter_failures_by_adapter": dict(self.adapter_failures_by_adapter),
            "adapter_failures_by_error_code": dict(
                self.adapter_failures_by_error_code
            ),
            "adapter_timeouts_by_adapter": dict(self.adapter_timeouts_by_adapter),
            "adapter_missing_count": self.adapter_missing_count,
            "adapter_missing_by_adapter": dict(self.adapter_missing_by_adapter),
            "capability_unresolved_count": self.capability_unresolved_count,
            "capability_unresolved_by_reason": dict(
                self.capability_unresolved_by_reason
            ),
            "capability_unresolved_by_behavior_node": dict(
                self.capability_unresolved_by_behavior_node
            ),
            "plan_rejection_count": self.plan_rejection_count,
            "plan_rejections_by_reason": dict(self.plan_rejections_by_reason),
            "plan_rejections_by_policy": dict(self.plan_rejections_by_policy),
            "plan_rejections_by_timing_anchor": dict(
                self.plan_rejections_by_timing_anchor
            ),
            "tool_rejection_count": self.tool_rejection_count,
            "tool_rejections_by_tool": dict(self.tool_rejections_by_tool),
            "tool_rejections_by_error_type": dict(self.tool_rejections_by_error_type),
            "latency_ms_by_intent": dict(self.latency_ms_by_intent),
            "average_latency_ms": self.average_latency_ms,
            "max_latency_ms": self.max_latency_ms,
        }


__all__ = ["RobotMetricsSnapshot"]
