"""Claude Agent SDK MCP tools for Reachy Mini actions."""

from __future__ import annotations

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


def action_allowed_tool_names(
    registry: ActionRegistry,
    *,
    server_name: str = "reachy_actions",
) -> list[str]:
    """Return SDK MCP allowed tool names for all registered actions."""
    return [
        f"mcp__{server_name}__{metadata.name}"
        for metadata in registry.list_metadata()
    ]


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


def _tool_error(text: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "is_error": True,
    }
