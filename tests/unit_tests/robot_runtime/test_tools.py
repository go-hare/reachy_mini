"""Tests for RobotRuntime model-facing intent tools."""

from __future__ import annotations

import json

import pytest

from reachy_mini.robot_runtime import (
    RobotIntentTools,
    RobotRuntime,
    RobotRuntimeConfig,
    RuntimeMode,
)
from reachy_mini.robot_runtime.adapters.mujoco import MujocoAdapter


@pytest.mark.asyncio
async def test_emit_embodied_intent_tool_accepts_generic_intent() -> None:
    """The model-facing API emits canonical embodied intents."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    tools = RobotIntentTools(runtime)

    result = await tools.emit_embodied_intent(
        intent_type="greet",
        modalities=["face"],
        speech_relation="before_speech",
        timing_anchor="speech_start",
        turn_id="turn_1",
    )

    assert result["intent"]["intent_type"] == "greet"
    assert result["event"]["event_type"] == "intent_received"
    assert result["event"]["turn_id"] == "turn_1"


@pytest.mark.asyncio
async def test_emit_embodied_intent_tool_returns_structured_validation_error() -> None:
    """Invalid model-facing intent args are structured and observable."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    tools = RobotIntentTools(runtime)

    result = await tools.emit_embodied_intent(
        intent_type="greet",
        modalities=["raw_motor"],
        turn_id="turn_invalid",
    )

    assert result["error"] == {
        "code": "tool_validation_failed",
        "type": "ValueError",
        "message": "modalities contains unsupported channels: ['raw_motor']",
    }
    assert result["event"]["event_type"] == "tool_rejected"
    assert result["event"]["source"] == "tool"
    assert result["event"]["turn_id"] == "turn_invalid"
    assert result["event"]["payload"] == {
        "tool": "emit_embodied_intent",
        "error_type": "ValueError",
        "intent_type": "greet",
    }
    assert runtime.trace_timeline(turn_id="turn_invalid").events[-1].event_type == (
        "tool_rejected"
    )
    assert runtime.trace_timeline(turn_id="turn_invalid").complete is True
    assert runtime.blackboard.snapshot()["pending_intents"] == []


@pytest.mark.asyncio
async def test_emit_embodied_intent_rejects_adapter_private_target_identifier() -> None:
    """Model-facing targets must not expose concrete adapter asset names."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    tools = RobotIntentTools(runtime)

    result = await tools.emit_embodied_intent(
        intent_type="greet",
        target={"motion": "HuiShou.motion3.json"},
        turn_id="turn_private_target",
    )

    assert result["error"] == {
        "code": "tool_validation_failed",
        "type": "ValueError",
        "message": (
            "target contains adapter-private identifier at target.motion: "
            ".motion3.json"
        ),
    }
    assert result["event"]["event_type"] == "tool_rejected"
    assert result["event"]["turn_id"] == "turn_private_target"
    assert runtime.blackboard.snapshot()["pending_intents"] == []


@pytest.mark.asyncio
async def test_emit_embodied_intent_rejects_adapter_private_constraint_field() -> None:
    """Model-facing constraints must remain body-agnostic."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    tools = RobotIntentTools(runtime)

    result = await tools.emit_embodied_intent(
        intent_type="greet",
        constraints={"command_type": "play_motion"},
        turn_id="turn_private_constraints",
    )

    assert result["error"] == {
        "code": "tool_validation_failed",
        "type": "ValueError",
        "message": (
            "constraints contains adapter-private field at "
            "constraints.command_type"
        ),
    }
    assert result["event"]["event_type"] == "tool_rejected"
    assert result["event"]["turn_id"] == "turn_private_constraints"
    assert runtime.blackboard.snapshot()["pending_intents"] == []


@pytest.mark.asyncio
async def test_emit_embodied_intent_rejects_adapter_private_adapter_id() -> None:
    """The model-facing API cannot route directly to a concrete adapter."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    tools = RobotIntentTools(runtime)

    result = await tools.emit_embodied_intent(
        intent_type="greet",
        constraints={"adapter_id": "mujoco"},
        turn_id="turn_private_adapter_id",
    )

    assert result["error"] == {
        "code": "tool_validation_failed",
        "type": "ValueError",
        "message": "constraints contains adapter-private field at constraints.adapter_id",
    }
    assert result["event"]["event_type"] == "tool_rejected"
    assert result["event"]["turn_id"] == "turn_private_adapter_id"
    assert runtime.blackboard.snapshot()["pending_intents"] == []


@pytest.mark.asyncio
async def test_emit_embodied_intent_rejects_adapter_private_reason() -> None:
    """Audit reasons cannot smuggle concrete implementation tool names."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    tools = RobotIntentTools(runtime)

    result = await tools.emit_embodied_intent(
        intent_type="greet",
        reason="call live2d_motion_huishou directly",
        turn_id="turn_private_reason",
    )

    assert result["error"] == {
        "code": "tool_validation_failed",
        "type": "ValueError",
        "message": (
            "reason contains adapter-private identifier at reason: "
            "live2d_motion_"
        ),
    }
    assert result["event"]["event_type"] == "tool_rejected"
    assert result["event"]["turn_id"] == "turn_private_reason"
    assert runtime.blackboard.snapshot()["pending_intents"] == []


@pytest.mark.asyncio
async def test_query_robot_state_tool_returns_snapshot() -> None:
    """The query tool returns a serializable state snapshot."""
    runtime = RobotRuntime()
    tools = RobotIntentTools(runtime)

    result = await tools.query_robot_state()

    assert result["revision"] >= 0
    assert result["state"]["mode"] == "avatar_only"


@pytest.mark.asyncio
async def test_query_robot_trace_tool_returns_complete_timeline() -> None:
    """The model-facing trace API exposes the Runtime telemetry chain."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    tools = RobotIntentTools(runtime)
    intent_result = await tools.emit_embodied_intent(
        intent_type="greet",
        modalities=["gesture"],
        turn_id="turn_trace",
    )

    trace = await tools.query_robot_trace(
        intent_id=str(intent_result["intent"]["intent_id"])
    )

    assert trace["complete"] is True
    assert trace["turn_ids"] == ["turn_trace"]
    assert trace["adapter_ids"] == ["mujoco"]
    assert trace["event_types"] == [
        "intent_received",
        "plan_scheduled",
        "command_resolved",
        "safety_decision",
        "command_started",
        "adapter_result",
    ]


@pytest.mark.asyncio
async def test_query_robot_trace_tool_returns_structured_error_without_identifier() -> None:
    """Trace queries require at least one id but return a tool error, not an exception."""
    runtime = RobotRuntime()
    tools = RobotIntentTools(runtime)

    result = await tools.query_robot_trace()

    assert result["error"]["code"] == "tool_validation_failed"
    assert result["error"]["type"] == "ValueError"
    assert "trace_timeline requires" in result["error"]["message"]
    assert result["event"]["event_type"] == "tool_rejected"
    assert result["event"]["payload"]["tool"] == "query_robot_trace"


@pytest.mark.asyncio
async def test_query_robot_trace_tool_treats_tool_rejection_as_complete() -> None:
    """Tool-layer validation failures are complete terminal traces by turn id."""
    runtime = RobotRuntime()
    tools = RobotIntentTools(runtime)
    await tools.emit_embodied_intent(
        intent_type="greet",
        modalities=["raw_motor"],
        turn_id="turn_tool_rejected",
    )

    trace = await tools.query_robot_trace(turn_id="turn_tool_rejected")

    assert trace["complete"] is True
    assert trace["gaps"] == []
    assert trace["event_types"] == ["tool_rejected"]
    assert trace["turn_ids"] == ["turn_tool_rejected"]
    assert trace["intent_ids"] == []
    assert trace["plan_ids"] == []


@pytest.mark.asyncio
async def test_query_robot_metrics_tool_returns_runtime_metrics() -> None:
    """The model-facing metrics API exposes derived Runtime counters."""
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION),
    )
    runtime.safety.limits = {"max_timeout_ms": 500}
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    tools = RobotIntentTools(runtime)
    await tools.emit_embodied_intent(
        intent_type="greet",
        modalities=["gesture"],
        turn_id="turn_metrics",
    )

    metrics = await tools.query_robot_metrics()

    assert metrics["intent_count"] == 1
    assert metrics["completed_intent_count"] == 1
    assert metrics["adapter_success_count"] == 1
    assert metrics["adapter_failure_count"] == 0
    assert metrics["adapter_results_by_status"] == {"completed": 1}
    assert metrics["adapter_results_by_adapter"] == {"mujoco": 1}
    assert metrics["safety_degrade_count"] == 1
    assert metrics["safety_degrades_by_reason"] == {"timeout_limited": 1}
    assert metrics["latency_ms_by_intent"]


@pytest.mark.asyncio
async def test_query_robot_metrics_tool_counts_validation_rejections() -> None:
    """Tool validation failures are visible in model-facing metrics."""
    runtime = RobotRuntime()
    tools = RobotIntentTools(runtime)

    await tools.emit_embodied_intent(intent_type="greet", modalities=["raw_motor"])
    await tools.query_robot_trace()

    metrics = await tools.query_robot_metrics()

    assert metrics["intent_count"] == 0
    assert metrics["tool_rejection_count"] == 2
    assert metrics["tool_rejections_by_tool"] == {
        "emit_embodied_intent": 1,
        "query_robot_trace": 1,
    }
    assert metrics["tool_rejections_by_error_type"] == {"ValueError": 2}


@pytest.mark.asyncio
async def test_query_robot_behavior_tree_tool_returns_node_snapshot() -> None:
    """The model-facing behavior tree API exposes node status indexes."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    tools = RobotIntentTools(runtime)
    await tools.emit_embodied_intent(
        intent_type="greet",
        modalities=["face"],
        turn_id="turn_tree",
    )
    runtime.tick_once()

    snapshot = await tools.query_robot_behavior_tree()

    assert snapshot["root_status"] == "SUCCESS"
    assert "RobotRuntimeRoot/TurnCoordinator" in snapshot["success_nodes"]
    assert snapshot["blackboard"]["active_plan"]["intent_type"] == "greet"
    assert snapshot["blackboard"]["robot_state"]["lifecycle"] == "active"


@pytest.mark.asyncio
async def test_query_robot_structured_log_tool_returns_jsonl_lines() -> None:
    """The model-facing structured log API exposes JSONL event records."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    tools = RobotIntentTools(runtime)
    await tools.emit_embodied_intent(
        intent_type="greet",
        modalities=["gesture"],
        turn_id="turn_log",
    )

    log = await tools.query_robot_structured_log(limit=1)
    payload = json.loads(log["lines"][0])

    assert log["format"] == "jsonl"
    assert log["schema_version"] == "robot_runtime.event.v1"
    assert log["line_count"] == 1
    assert payload["event_type"] == "adapter_result"
    assert payload["trace"]["turn_id"] == "turn_log"
    assert payload["payload"]["adapter_id"] == "mujoco"


@pytest.mark.asyncio
async def test_request_robot_task_preserves_explicit_task_type_owner() -> None:
    """The task_type argument owns task semantics over target metadata."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    tools = RobotIntentTools(runtime)

    result = await tools.request_robot_task(
        task_type="inspect",
        target={"task_id": "task_1", "task_type": "spoofed"},
        constraints={"max_duration_ms": 500},
        turn_id="turn_task_owner",
    )

    assert "error" not in result
    assert result["intent"]["intent_type"] == "task_execute"
    assert result["intent"]["target"] == {
        "task_id": "task_1",
        "task_type": "inspect",
    }
    assert result["events"][-1]["event_type"] == "plan_scheduled"


@pytest.mark.asyncio
async def test_request_robot_task_returns_structured_error_for_non_object_target() -> None:
    """Task target must remain a structured object for field ownership."""
    runtime = RobotRuntime()
    tools = RobotIntentTools(runtime)

    result = await tools.request_robot_task(
        task_type="inspect",
        target="not-object",
        turn_id="turn_bad_task",
    )

    assert result["error"] == {
        "code": "tool_validation_failed",
        "type": "ValueError",
        "message": "target must be an object",
    }
    assert result["event"]["event_type"] == "tool_rejected"
    assert result["event"]["payload"] == {
        "tool": "request_robot_task",
        "error_type": "ValueError",
        "task_type": "inspect",
    }
    assert runtime.trace_timeline(turn_id="turn_bad_task").events[-1].event_type == (
        "tool_rejected"
    )


@pytest.mark.asyncio
async def test_request_robot_task_rejects_adapter_private_target_field() -> None:
    """Task requests cannot bypass the intent facade with raw robot fields."""
    runtime = RobotRuntime()
    tools = RobotIntentTools(runtime)

    result = await tools.request_robot_task(
        task_type="inspect",
        target={"motor_name": "body_rotation"},
        turn_id="turn_private_task_target",
    )

    assert result["error"] == {
        "code": "tool_validation_failed",
        "type": "ValueError",
        "message": "target contains adapter-private field at target.motor_name",
    }
    assert result["event"]["event_type"] == "tool_rejected"
    assert result["event"]["turn_id"] == "turn_private_task_target"
    assert runtime.blackboard.snapshot()["pending_intents"] == []


@pytest.mark.asyncio
async def test_request_robot_task_returns_structured_error_for_empty_task_type() -> None:
    """Task type is a required semantic field, not optional metadata."""
    runtime = RobotRuntime()
    tools = RobotIntentTools(runtime)

    result = await tools.request_robot_task(
        task_type=" ",
        target={"task_id": "task_1"},
        turn_id="turn_empty_task_type",
    )

    assert result["error"] == {
        "code": "tool_validation_failed",
        "type": "ValueError",
        "message": "task_type must be a non-empty string",
    }
    assert result["event"]["event_type"] == "tool_rejected"
    assert result["event"]["turn_id"] == "turn_empty_task_type"
    assert result["event"]["payload"]["tool"] == "request_robot_task"


@pytest.mark.asyncio
async def test_cancel_robot_task_tool_uses_runtime_cancel_path() -> None:
    """The model-facing cancel API returns a structured Runtime cancellation event."""
    runtime = RobotRuntime()
    tools = RobotIntentTools(runtime)

    result = await tools.cancel_robot_task(task_id="missing", reason="operator")

    assert result["event_type"] == "task_cancel_requested"
    assert result["status"] == "not_found"
    assert result["payload"]["task_id"] == "missing"
    assert result["reason"] == "operator"


@pytest.mark.asyncio
async def test_cancel_robot_task_tool_rejects_empty_task_id() -> None:
    """Cancellation requires a non-empty task, plan, command, or step id."""
    runtime = RobotRuntime()
    tools = RobotIntentTools(runtime)

    result = await tools.cancel_robot_task(task_id=" ")

    assert result["error"] == {
        "code": "tool_validation_failed",
        "type": "ValueError",
        "message": "task_id must be a non-empty string",
    }
    assert result["event"]["event_type"] == "tool_rejected"
    assert result["event"]["payload"] == {
        "tool": "cancel_robot_task",
        "error_type": "ValueError",
        "task_id": " ",
    }
