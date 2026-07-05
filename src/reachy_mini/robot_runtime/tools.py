"""Model-facing Robot Intent API helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from reachy_mini.robot_runtime.body_agnostic import validate_body_agnostic_value
from reachy_mini.robot_runtime.contracts import EmbodiedIntent
from reachy_mini.robot_runtime.runtime import RobotRuntime


@dataclass(slots=True)
class RobotIntentTools:
    """Stable intent API that can be exposed to an LLM tool layer."""

    runtime: RobotRuntime

    async def emit_embodied_intent(
        self,
        *,
        intent_type: str,
        affect: str | None = None,
        target: dict[str, Any] | None = None,
        intensity: float = 0.5,
        priority: int = 40,
        speech_relation: str = "idle",
        timing_anchor: str | None = None,
        modalities: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        reason: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Create and submit a body-agnostic embodied intent."""
        try:
            normalized_target = _copy_optional_mapping("target", target)
            normalized_constraints = _copy_mapping("constraints", constraints)
            validate_body_agnostic_value("target", normalized_target)
            validate_body_agnostic_value("constraints", normalized_constraints)
            validate_body_agnostic_value("reason", reason)
            intent = EmbodiedIntent(
                intent_type=intent_type,
                affect=affect,
                target=normalized_target,
                intensity=intensity,
                priority=priority,
                speech_relation=speech_relation,
                timing_anchor=timing_anchor,
                modalities=list(modalities or []),
                constraints=normalized_constraints,
                reason=reason,
                turn_id=turn_id,
            )
        except ValueError as exc:
            event = await self.runtime.record_tool_error(
                tool_name="emit_embodied_intent",
                exc=exc,
                turn_id=turn_id,
                payload={"intent_type": intent_type},
            )
            return _tool_error_response(exc, event)
        events = await self.runtime.handle_intent(intent)
        return {
            "intent": intent.to_dict(),
            "event": events[0].to_dict(),
            "events": [event.to_dict() for event in events],
        }

    async def query_robot_state(self) -> dict[str, Any]:
        """Return the current RobotRuntime state snapshot."""
        snapshot = self.runtime.snapshot()
        return {
            "revision": snapshot.revision,
            "created_at_ms": snapshot.created_at_ms,
            "state": snapshot.state.to_dict(),
        }

    async def query_robot_trace(
        self,
        *,
        turn_id: str = "",
        intent_id: str = "",
        plan_id: str = "",
        command_id: str = "",
    ) -> dict[str, Any]:
        """Return the observable timeline for one robot turn, intent, plan, or command."""
        try:
            timeline = self.runtime.trace_timeline(
                turn_id=turn_id or None,
                intent_id=intent_id or None,
                plan_id=plan_id or None,
                command_id=command_id or None,
            )
        except ValueError as exc:
            event = await self.runtime.record_tool_error(
                tool_name="query_robot_trace",
                exc=exc,
                turn_id=turn_id,
                payload={
                    "intent_id": intent_id,
                    "plan_id": plan_id,
                    "command_id": command_id,
                },
            )
            return _tool_error_response(exc, event)
        return timeline.to_dict()

    async def query_robot_metrics(self) -> dict[str, Any]:
        """Return derived RobotRuntime observability metrics."""
        return self.runtime.metrics().to_dict()

    async def query_robot_behavior_tree(self) -> dict[str, object]:
        """Return current behavior tree node status and blackboard snapshot."""
        return self.runtime.behavior_tree_snapshot()

    async def query_robot_structured_log(self, *, limit: int = 100) -> dict[str, Any]:
        """Return recent Runtime events as structured JSONL strings."""
        lines = self.runtime.structured_log_lines(limit=limit)
        return {
            "format": "jsonl",
            "schema_version": "robot_runtime.event.v1",
            "line_count": len(lines),
            "lines": lines,
        }

    async def request_robot_task(
        self,
        *,
        task_type: str,
        target: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Submit a long-running task request as a canonical embodied intent."""
        try:
            normalized_task_type = _required_text("task_type", task_type)
            normalized_target = _copy_mapping("target", target)
            normalized_constraints = _copy_mapping("constraints", constraints)
            validate_body_agnostic_value("target", normalized_target)
            validate_body_agnostic_value("constraints", normalized_constraints)
        except ValueError as exc:
            event = await self.runtime.record_tool_error(
                tool_name="request_robot_task",
                exc=exc,
                turn_id=turn_id,
                payload={"task_type": task_type},
            )
            return _tool_error_response(exc, event)
        normalized_target["task_type"] = normalized_task_type
        return await self.emit_embodied_intent(
            intent_type="task_execute",
            target=normalized_target,
            priority=60,
            speech_relation="after_speech",
            timing_anchor="task_start",
            constraints=normalized_constraints,
            turn_id=turn_id,
        )

    async def cancel_robot_task(self, *, task_id: str, reason: str = "") -> dict[str, Any]:
        """Cancel a scheduled plan or active command by plan id, command id, or task id."""
        try:
            normalized_task_id = _required_text("task_id", task_id)
        except ValueError as exc:
            event = await self.runtime.record_tool_error(
                tool_name="cancel_robot_task",
                exc=exc,
                payload={"task_id": task_id},
            )
            return _tool_error_response(exc, event)
        event = await self.runtime.cancel_task(normalized_task_id, reason=reason)
        return event.to_dict()


def _tool_error_response(exc: Exception, event: Any) -> dict[str, Any]:
    return {
        "error": {
            "code": "tool_validation_failed",
            "type": type(exc).__name__,
            "message": str(exc),
        },
        "event": event.to_dict(),
        "events": [event.to_dict()],
    }


def _copy_optional_mapping(
    field_name: str,
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    return _copy_mapping(field_name, value)


def _copy_mapping(
    field_name: str,
    value: dict[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _required_text(field_name: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


__all__ = ["RobotIntentTools"]
