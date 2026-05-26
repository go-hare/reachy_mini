"""Tests for the Claude Agent SDK MCP action server."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import types

from reachy_mini.action_runtime import ActionResult
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.reachy_brain.mcp_server import (
    action_allowed_tool_names,
    create_action_mcp_server,
)


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
