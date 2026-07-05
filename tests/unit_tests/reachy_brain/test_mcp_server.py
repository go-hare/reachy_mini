"""Tests for the Claude Agent SDK MCP action server."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import types

from reachy_mini.action_runtime import ActionResult
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.reachy_brain.mcp_server import (
    action_allowed_tool_names,
    create_action_mcp_server,
    create_robot_mcp_server,
    robot_allowed_tool_names,
)
from reachy_mini.robot_runtime import (
    RobotIntentTools,
    RobotRuntime,
    RobotRuntimeConfig,
    RuntimeMode,
)
from reachy_mini.robot_runtime.adapters.mujoco import MujocoAdapter
from reachy_mini.robot_runtime.adapters.ros2 import ROS2Adapter


async def _list_tools(server: Any) -> list[types.Tool]:
    result = await server.request_handlers[types.ListToolsRequest](
        types.ListToolsRequest()
    )
    return result.root.tools


async def _call_tool(
    server: Any,
    name: str,
    arguments: dict[str, Any],
) -> types.CallToolResult:
    result = await server.request_handlers[types.CallToolRequest](
        types.CallToolRequest(
            params=types.CallToolRequestParams(name=name, arguments=arguments)
        )
    )
    return result.root


@pytest.mark.asyncio
async def test_action_mcp_tool_runs_action_runtime_facade() -> None:
    """SDK MCP tools validate params and call the injected action runner."""
    registry = create_builtin_registry()
    calls = []

    async def run_action(spec):
        calls.append(spec)
        return ActionResult(
            request_id=spec.request_id,
            action_id="a1",
            name=spec.name,
            owner_id=spec.owner_id,
            status="ok",
            duration_ms=3,
        )

    server_config = create_action_mcp_server(
        registry=registry,
        run_action=run_action,
    )
    server = server_config["instance"]
    tools = await _list_tools(server)
    nod_tool = next(tool for tool in tools if tool.name == "nod")

    result = await _call_tool(
        server,
        "nod",
        {"cycles": 1, "period_s": 0.3},
    )

    assert nod_tool.name == "nod"
    assert calls[0].name == "nod"
    assert calls[0].owner_id == "main-agent"
    assert result.isError is False
    assert "Action nod ok" in result.content[0].text


@pytest.mark.asyncio
async def test_action_mcp_tool_rejects_invalid_params_before_runner() -> None:
    """Bad MCP action args return SDK tool errors without touching hardware."""
    registry = create_builtin_registry()
    calls: list[Any] = []

    async def run_action(spec):
        calls.append(spec)
        raise AssertionError("runner should not be called")

    server_config = create_action_mcp_server(
        registry=registry,
        run_action=run_action,
    )
    server = server_config["instance"]

    result = await _call_tool(
        server,
        "nod",
        {"cycles": 99},
    )

    assert calls == []
    assert result.isError is True
    assert "Input validation error" in result.content[0].text


def test_allowed_tool_names_use_sdk_mcp_prefix() -> None:
    """Action tools use the SDK mcp__server__tool naming convention."""
    names = action_allowed_tool_names(create_builtin_registry())

    assert "mcp__reachy_actions__nod" in names
    assert "mcp__reachy_actions__set_antenna" in names


@pytest.mark.asyncio
async def test_robot_mcp_tool_emits_body_agnostic_intent() -> None:
    """Robot Intent API is exposed as SDK MCP tools."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    server_config = create_robot_mcp_server(robot_tools=RobotIntentTools(runtime))
    server = server_config["instance"]

    tools = await _list_tools(server)
    result = await _call_tool(
        server,
        "emit_embodied_intent",
        {
            "intent_type": "greet",
            "modalities": ["face"],
            "speech_relation": "before_speech",
            "timing_anchor": "speech_start",
            "turn_id": "turn_1",
        },
    )

    assert "emit_embodied_intent" in [tool.name for tool in tools]
    assert result.isError is False
    assert '"intent_type": "greet"' in result.content[0].text
    assert runtime.blackboard.snapshot()["pending_intents"][0].turn_id == "turn_1"


@pytest.mark.asyncio
async def test_robot_mcp_tool_returns_structured_error_for_invalid_intent() -> None:
    """Robot MCP tool validation failures are JSON errors and Runtime events."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    server_config = create_robot_mcp_server(robot_tools=RobotIntentTools(runtime))
    server = server_config["instance"]

    result = await _call_tool(
        server,
        "emit_embodied_intent",
        {
            "intent_type": "greet",
            "modalities": ["raw_motor"],
            "turn_id": "turn_invalid",
        },
    )
    payload = json.loads(result.content[0].text)

    assert result.isError is True
    assert payload["error"]["code"] == "tool_validation_failed"
    assert payload["event"]["event_type"] == "tool_rejected"
    assert payload["event"]["turn_id"] == "turn_invalid"
    assert payload["event"]["payload"]["tool"] == "emit_embodied_intent"


@pytest.mark.asyncio
async def test_robot_mcp_tool_queries_trace_timeline() -> None:
    """Robot MCP exposes trace timeline lookup for observability."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    server_config = create_robot_mcp_server(robot_tools=RobotIntentTools(runtime))
    server = server_config["instance"]
    await _call_tool(
        server,
        "emit_embodied_intent",
        {
            "intent_type": "greet",
            "modalities": ["gesture"],
            "turn_id": "turn_trace",
        },
    )

    tools = await _list_tools(server)
    result = await _call_tool(server, "query_robot_trace", {"turn_id": "turn_trace"})
    payload = json.loads(result.content[0].text)

    assert "query_robot_trace" in [tool.name for tool in tools]
    assert result.isError is False
    assert payload["complete"] is True
    assert payload["turn_ids"] == ["turn_trace"]
    assert payload["adapter_ids"] == ["mujoco"]


@pytest.mark.asyncio
async def test_robot_mcp_tool_queries_runtime_metrics() -> None:
    """Robot MCP exposes derived Runtime metrics."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    server_config = create_robot_mcp_server(robot_tools=RobotIntentTools(runtime))
    server = server_config["instance"]
    await _call_tool(
        server,
        "emit_embodied_intent",
        {
            "intent_type": "greet",
            "modalities": ["gesture"],
            "turn_id": "turn_metrics",
        },
    )

    tools = await _list_tools(server)
    result = await _call_tool(server, "query_robot_metrics", {})
    payload = json.loads(result.content[0].text)

    assert "query_robot_metrics" in [tool.name for tool in tools]
    assert result.isError is False
    assert payload["intent_count"] == 1
    assert payload["adapter_success_count"] == 1
    assert payload["adapter_failure_count"] == 0


@pytest.mark.asyncio
async def test_robot_mcp_tool_queries_behavior_tree_snapshot() -> None:
    """Robot MCP exposes behavior tree node status snapshots."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    server_config = create_robot_mcp_server(robot_tools=RobotIntentTools(runtime))
    server = server_config["instance"]
    await _call_tool(
        server,
        "emit_embodied_intent",
        {
            "intent_type": "greet",
            "modalities": ["face"],
            "turn_id": "turn_tree",
        },
    )
    runtime.tick_once()

    tools = await _list_tools(server)
    result = await _call_tool(server, "query_robot_behavior_tree", {})
    payload = json.loads(result.content[0].text)

    assert "query_robot_behavior_tree" in [tool.name for tool in tools]
    assert result.isError is False
    assert payload["root_status"] == "SUCCESS"
    assert "RobotRuntimeRoot/TurnCoordinator" in payload["success_nodes"]
    assert payload["blackboard"]["active_plan"]["intent_type"] == "greet"


@pytest.mark.asyncio
async def test_robot_mcp_tool_queries_structured_log() -> None:
    """Robot MCP exposes structured JSONL event logs."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    server_config = create_robot_mcp_server(robot_tools=RobotIntentTools(runtime))
    server = server_config["instance"]
    await _call_tool(
        server,
        "emit_embodied_intent",
        {
            "intent_type": "greet",
            "modalities": ["gesture"],
            "turn_id": "turn_log",
        },
    )

    tools = await _list_tools(server)
    result = await _call_tool(server, "query_robot_structured_log", {"limit": 1})
    payload = json.loads(result.content[0].text)
    record = json.loads(payload["lines"][0])

    assert "query_robot_structured_log" in [tool.name for tool in tools]
    assert result.isError is False
    assert payload["format"] == "jsonl"
    assert payload["line_count"] == 1
    assert record["event_type"] == "adapter_result"
    assert record["trace"]["turn_id"] == "turn_log"


@pytest.mark.asyncio
async def test_robot_mcp_tool_requests_and_cancels_task() -> None:
    """Robot MCP exposes task request and cancellation tools."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.HYBRID))
    await runtime.activate()
    await runtime.register_adapter(ROS2Adapter())
    server_config = create_robot_mcp_server(robot_tools=RobotIntentTools(runtime))
    server = server_config["instance"]

    request_result = await _call_tool(
        server,
        "request_robot_task",
            {
                "task_type": "inspect",
                "target": {"task_id": "task_1", "task_type": "spoofed"},
                "constraints": {"max_duration_ms": 500},
                "turn_id": "turn_task",
            },
        )
    cancel_result = await _call_tool(
        server,
        "cancel_robot_task",
        {"task_id": "task_1", "reason": "operator"},
    )
    request_payload = json.loads(request_result.content[0].text)
    cancel_payload = json.loads(cancel_result.content[0].text)

    assert request_result.isError is False
    assert request_payload["intent"]["intent_type"] == "task_execute"
    assert request_payload["intent"]["target"]["task_type"] == "inspect"
    assert request_payload["events"][-1]["event_type"] == "plan_scheduled"
    assert request_payload["events"][-1]["payload"]["behavior_node"] == (
        "RobotPolicyEngine/task_policy"
    )
    assert cancel_result.isError is False
    assert cancel_payload["event_type"] == "task_cancel_requested"
    assert cancel_payload["status"] == "accepted"
    assert cancel_payload["payload"]["task_id"] == "task_1"
    assert cancel_payload["payload"]["cancelled_plan_ids"]
    assert runtime.scheduler.list_scheduled() == []


@pytest.mark.asyncio
async def test_robot_mcp_task_tools_require_non_empty_ids_in_schema() -> None:
    """Robot task MCP schemas expose non-empty required string constraints."""
    runtime = RobotRuntime()
    server_config = create_robot_mcp_server(robot_tools=RobotIntentTools(runtime))
    server = server_config["instance"]

    tools = await _list_tools(server)
    request_tool = next(tool for tool in tools if tool.name == "request_robot_task")
    cancel_tool = next(tool for tool in tools if tool.name == "cancel_robot_task")

    assert request_tool.inputSchema["properties"]["task_type"]["minLength"] == 1
    assert cancel_tool.inputSchema["properties"]["task_id"]["minLength"] == 1


@pytest.mark.asyncio
async def test_robot_mcp_schemas_describe_body_agnostic_boundaries() -> None:
    """Robot MCP schemas warn the model away from adapter-private fields."""
    runtime = RobotRuntime()
    server_config = create_robot_mcp_server(robot_tools=RobotIntentTools(runtime))
    server = server_config["instance"]

    tools = await _list_tools(server)
    emit_tool = next(tool for tool in tools if tool.name == "emit_embodied_intent")
    task_tool = next(tool for tool in tools if tool.name == "request_robot_task")

    assert "adapter_id" in emit_tool.description
    assert "command_type" in emit_tool.description
    assert "adapter_id" in task_tool.description
    assert "command_type" in task_tool.description
    assert "adapter_id" in emit_tool.inputSchema["properties"]["target"]["description"]
    assert (
        "command_type"
        in emit_tool.inputSchema["properties"]["constraints"]["description"]
    )
    assert "adapter_id" in task_tool.inputSchema["properties"]["target"]["description"]
    assert (
        "command_type"
        in task_tool.inputSchema["properties"]["constraints"]["description"]
    )


def test_robot_allowed_tool_names_use_sdk_mcp_prefix() -> None:
    """Robot tools use the SDK mcp__server__tool naming convention."""
    names = robot_allowed_tool_names()

    assert "emit_embodied_intent" in names
    assert "mcp__reachy_robot__emit_embodied_intent" in names
    assert "query_robot_trace" in names
    assert "mcp__reachy_robot__query_robot_trace" in names
    assert "query_robot_metrics" in names
    assert "mcp__reachy_robot__query_robot_metrics" in names
    assert "query_robot_behavior_tree" in names
    assert "mcp__reachy_robot__query_robot_behavior_tree" in names
    assert "query_robot_structured_log" in names
    assert "mcp__reachy_robot__query_robot_structured_log" in names
    assert "request_robot_task" in names
    assert "mcp__reachy_robot__request_robot_task" in names
    assert "cancel_robot_task" in names
    assert "mcp__reachy_robot__cancel_robot_task" in names
