"""Claude Agent SDK MCP tools for Reachy Mini actions."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from reachy_mini.action_runtime import ActionResult, ActionSpec
from reachy_mini.action_runtime.errors import ActionRuntimeError
from reachy_mini.action_runtime.registry import ActionRegistry

SDK_INSTALL_HINT = (
    "Install Claude Agent SDK with: "
    "conda run -n reachy python -m pip install claude-agent-sdk"
)

ActionRunner = Callable[[ActionSpec], Awaitable[ActionResult]]
RobotToolRunner = Any


class ClaudeAgentSDKUnavailableError(RuntimeError):
    """Raised when the Claude Agent SDK dependency is not importable."""


def require_claude_agent_sdk() -> dict[str, Any]:
    """Return SDK symbols or raise an actionable installation error."""
    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except ModuleNotFoundError as exc:
        raise ClaudeAgentSDKUnavailableError(SDK_INSTALL_HINT) from exc
    return {
        "create_sdk_mcp_server": create_sdk_mcp_server,
        "tool": tool,
    }


def create_action_mcp_server(
    *,
    registry: ActionRegistry,
    run_action: ActionRunner,
    owner_id: str = "main-agent",
    server_name: str = "reachy_actions",
) -> Any:
    """Expose registered ActionRuntime actions as SDK MCP tools."""
    sdk = require_claude_agent_sdk()
    tool = sdk["tool"]
    create_sdk_mcp_server = sdk["create_sdk_mcp_server"]
    tools = [
        _build_action_tool(
            registry=registry,
            run_action=run_action,
            owner_id=owner_id,
            tool_factory=tool,
            action_name=metadata.name,
        )
        for metadata in registry.list_metadata()
    ]
    return create_sdk_mcp_server(name=server_name, version="1.0.0", tools=tools)


def create_robot_mcp_server(
    *,
    robot_tools: RobotToolRunner,
    server_name: str = "reachy_robot",
) -> Any:
    """Expose the body-agnostic Robot Intent API as SDK MCP tools."""
    sdk = require_claude_agent_sdk()
    tool = sdk["tool"]
    create_sdk_mcp_server = sdk["create_sdk_mcp_server"]
    tools = [
        _build_emit_embodied_intent_tool(robot_tools=robot_tools, tool_factory=tool),
        _build_query_robot_state_tool(robot_tools=robot_tools, tool_factory=tool),
        _build_query_robot_trace_tool(robot_tools=robot_tools, tool_factory=tool),
        _build_query_robot_metrics_tool(robot_tools=robot_tools, tool_factory=tool),
        _build_query_robot_behavior_tree_tool(robot_tools=robot_tools, tool_factory=tool),
        _build_query_robot_structured_log_tool(robot_tools=robot_tools, tool_factory=tool),
        _build_request_robot_task_tool(robot_tools=robot_tools, tool_factory=tool),
        _build_cancel_robot_task_tool(robot_tools=robot_tools, tool_factory=tool),
    ]
    return create_sdk_mcp_server(name=server_name, version="1.0.0", tools=tools)


def action_allowed_tool_names(
    registry: ActionRegistry,
    *,
    server_name: str = "reachy_actions",
) -> list[str]:
    """Return SDK MCP allowed tool names for all registered actions."""
    names: list[str] = []
    for metadata in registry.list_metadata():
        # Claude Agent SDK examples use bare SDK MCP tool names, while the
        # bundled CLI may expose MCP tools with a server-qualified name.
        names.append(metadata.name)
        names.append(f"mcp__{server_name}__{metadata.name}")
    return names


def robot_allowed_tool_names(*, server_name: str = "reachy_robot") -> list[str]:
    """Return SDK MCP allowed tool names for Robot Intent API tools."""
    names: list[str] = []
    for name in (
        "emit_embodied_intent",
        "query_robot_state",
        "query_robot_trace",
        "query_robot_metrics",
        "query_robot_behavior_tree",
        "query_robot_structured_log",
        "request_robot_task",
        "cancel_robot_task",
    ):
        names.append(name)
        names.append(f"mcp__{server_name}__{name}")
    return names


def _build_action_tool(
    *,
    registry: ActionRegistry,
    run_action: ActionRunner,
    owner_id: str,
    tool_factory: Callable[..., Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], Any]],
    action_name: str,
) -> Any:
    metadata = registry.get_metadata(action_name)

    @tool_factory(
        metadata.name,
        metadata.description,
        metadata.parameter_schema,
    )
    async def action_tool(args: dict[str, Any]) -> dict[str, Any]:
        spec = ActionSpec(
            name=metadata.name,
            params=dict(args or {}),
            reason=f"sdk_mcp:{metadata.name}",
            request_id=f"mcp_{uuid.uuid4().hex}",
            owner_id=owner_id,
            priority=metadata.default_priority,
            interruptible=metadata.default_interruptible,
            deadline_s=metadata.default_duration_s,
        )
        try:
            registry.build(spec)
        except ActionRuntimeError as exc:
            return _tool_error(f"{type(exc).__name__}: {exc}")

        try:
            result = await run_action(spec)
        except Exception as exc:  # pragma: no cover - defensive SDK tool boundary
            return _tool_error(f"{type(exc).__name__}: {exc}")
        text = (
            f"Action {result.name} {result.status}"
            f" request_id={result.request_id}"
            f" duration_ms={result.duration_ms}"
        )
        if result.error:
            text = f"{text} error={result.error}"
        return {
            "content": [{"type": "text", "text": text}],
            "is_error": result.status != "ok",
        }

    return action_tool


def _build_emit_embodied_intent_tool(
    *,
    robot_tools: RobotToolRunner,
    tool_factory: Callable[..., Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], Any]],
) -> Any:
    @tool_factory(
        "emit_embodied_intent",
        (
            "Submit a body-agnostic robot intent. Do not provide adapter_id, "
            "command_type, Live2D files, raw motor names, or joint names."
        ),
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "intent_type": {"type": "string"},
                "affect": {"type": ["string", "null"]},
                "target": {
                    "type": ["object", "null"],
                    "additionalProperties": True,
                    "description": (
                        "Semantic target only; no adapter_id, command_type, "
                        "motion files, motor names, or joint names."
                    ),
                },
                "intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "priority": {"type": "integer", "minimum": 0, "maximum": 100},
                "speech_relation": {"type": "string"},
                "timing_anchor": {"type": ["string", "null"]},
                "modalities": {"type": "array", "items": {"type": "string"}},
                "constraints": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": (
                        "Semantic timing/safety constraints only; no adapter_id, "
                        "command_type, raw command, motor, or joint fields."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Audit reason without adapter-private identifiers.",
                },
                "turn_id": {"type": "string"},
            },
            "required": ["intent_type"],
        },
    )
    async def emit_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await robot_tools.emit_embodied_intent(**dict(args or {}))
        except Exception as exc:  # pragma: no cover - defensive SDK tool boundary
            return _tool_error(f"{type(exc).__name__}: {exc}")
        return _tool_json(result, is_error=_is_tool_error(result))

    return emit_tool


def _build_query_robot_state_tool(
    *,
    robot_tools: RobotToolRunner,
    tool_factory: Callable[..., Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], Any]],
) -> Any:
    @tool_factory(
        "query_robot_state",
        "Return the current RobotRuntime state snapshot.",
        {"type": "object", "additionalProperties": False, "properties": {}},
    )
    async def query_tool(_args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await robot_tools.query_robot_state()
        except Exception as exc:  # pragma: no cover - defensive SDK tool boundary
            return _tool_error(f"{type(exc).__name__}: {exc}")
        return _tool_json(result, is_error=_is_tool_error(result))

    return query_tool


def _build_query_robot_trace_tool(
    *,
    robot_tools: RobotToolRunner,
    tool_factory: Callable[..., Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], Any]],
) -> Any:
    @tool_factory(
        "query_robot_trace",
        "Return the RobotRuntime trace timeline for one turn, intent, plan, or command.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "turn_id": {"type": "string"},
                "intent_id": {"type": "string"},
                "plan_id": {"type": "string"},
                "command_id": {"type": "string"},
            },
            "anyOf": [
                {"required": ["turn_id"]},
                {"required": ["intent_id"]},
                {"required": ["plan_id"]},
                {"required": ["command_id"]},
            ],
        },
    )
    async def trace_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await robot_tools.query_robot_trace(**dict(args or {}))
        except Exception as exc:  # pragma: no cover - defensive SDK tool boundary
            return _tool_error(f"{type(exc).__name__}: {exc}")
        return _tool_json(result, is_error=_is_tool_error(result))

    return trace_tool


def _build_query_robot_metrics_tool(
    *,
    robot_tools: RobotToolRunner,
    tool_factory: Callable[..., Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], Any]],
) -> Any:
    @tool_factory(
        "query_robot_metrics",
        "Return derived RobotRuntime metrics for intents, safety, adapters, and latency.",
        {"type": "object", "additionalProperties": False, "properties": {}},
    )
    async def metrics_tool(_args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await robot_tools.query_robot_metrics()
        except Exception as exc:  # pragma: no cover - defensive SDK tool boundary
            return _tool_error(f"{type(exc).__name__}: {exc}")
        return _tool_json(result, is_error=_is_tool_error(result))

    return metrics_tool


def _build_query_robot_behavior_tree_tool(
    *,
    robot_tools: RobotToolRunner,
    tool_factory: Callable[..., Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], Any]],
) -> Any:
    @tool_factory(
        "query_robot_behavior_tree",
        "Return current RobotRuntime behavior tree node status and blackboard snapshot.",
        {"type": "object", "additionalProperties": False, "properties": {}},
    )
    async def behavior_tree_tool(_args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await robot_tools.query_robot_behavior_tree()
        except Exception as exc:  # pragma: no cover - defensive SDK tool boundary
            return _tool_error(f"{type(exc).__name__}: {exc}")
        return _tool_json(result, is_error=_is_tool_error(result))

    return behavior_tree_tool


def _build_query_robot_structured_log_tool(
    *,
    robot_tools: RobotToolRunner,
    tool_factory: Callable[..., Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], Any]],
) -> Any:
    @tool_factory(
        "query_robot_structured_log",
        "Return recent RobotRuntime events as structured JSONL records.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "limit": {"type": "integer", "minimum": 0, "maximum": 1000},
            },
        },
    )
    async def structured_log_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await robot_tools.query_robot_structured_log(**dict(args or {}))
        except Exception as exc:  # pragma: no cover - defensive SDK tool boundary
            return _tool_error(f"{type(exc).__name__}: {exc}")
        return _tool_json(result, is_error=_is_tool_error(result))

    return structured_log_tool


def _build_request_robot_task_tool(
    *,
    robot_tools: RobotToolRunner,
    tool_factory: Callable[..., Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], Any]],
) -> Any:
    @tool_factory(
        "request_robot_task",
        (
            "Request a long-running robot task through the body-agnostic intent "
            "API. Do not provide adapter_id, command_type, raw motor names, or "
            "joint names."
        ),
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "task_type": {"type": "string", "minLength": 1},
                "target": {
                    "type": ["object", "null"],
                    "additionalProperties": True,
                    "description": (
                        "Semantic task target only; no adapter_id, command_type, "
                        "motion files, motor names, or joint names."
                    ),
                },
                "constraints": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": (
                        "Semantic task constraints only; no adapter_id, "
                        "command_type, raw command, motor, or joint fields."
                    ),
                },
                "turn_id": {"type": "string"},
            },
            "required": ["task_type"],
        },
    )
    async def task_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await robot_tools.request_robot_task(**dict(args or {}))
        except Exception as exc:  # pragma: no cover - defensive SDK tool boundary
            return _tool_error(f"{type(exc).__name__}: {exc}")
        return _tool_json(result, is_error=_is_tool_error(result))

    return task_tool


def _build_cancel_robot_task_tool(
    *,
    robot_tools: RobotToolRunner,
    tool_factory: Callable[..., Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], Any]],
) -> Any:
    @tool_factory(
        "cancel_robot_task",
        "Cancel a robot task by task id.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "task_id": {"type": "string", "minLength": 1},
                "reason": {"type": "string"},
            },
            "required": ["task_id"],
        },
    )
    async def cancel_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await robot_tools.cancel_robot_task(**dict(args or {}))
        except Exception as exc:  # pragma: no cover - defensive SDK tool boundary
            return _tool_error(f"{type(exc).__name__}: {exc}")
        return _tool_json(result, is_error=_is_tool_error(result))

    return cancel_tool


def _tool_json(data: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data, ensure_ascii=False, sort_keys=True),
            }
        ],
        "is_error": is_error,
    }


def _tool_error(text: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "is_error": True,
    }


def _is_tool_error(result: dict[str, Any]) -> bool:
    return isinstance(result.get("error"), dict)
