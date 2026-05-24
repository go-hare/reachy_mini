"""Tests for v4 Brain tool adapter."""

from __future__ import annotations

import pytest

from reachy_mini.action_runtime import ActionParamError
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.reachy_brain.tool_adapter import (
    ToolCall,
    action_tools,
    tool_call_to_action_spec,
)


def test_action_tools_hide_locks_and_expose_schema() -> None:
    """Tool definitions expose LLM-safe action metadata only."""
    registry = create_builtin_registry()

    tools = {tool.name: tool for tool in action_tools(registry)}

    assert "nod" in tools
    assert tools["nod"].input_schema["type"] == "object"
    assert "required_locks" not in tools["nod"].metadata
    assert "safety_notes" not in tools["nod"].metadata


def test_tool_call_to_action_spec_injects_owner_request_and_priority() -> None:
    """Adapter converts tool calls to validated ActionSpec objects."""
    registry = create_builtin_registry()

    spec = tool_call_to_action_spec(
        ToolCall(name="nod", arguments={"cycles": 2}, reason="ack"),
        registry=registry,
        request_id="r1",
        owner_id="main-agent",
    )

    assert spec.name == "nod"
    assert spec.params == {"cycles": 2}
    assert spec.request_id == "r1"
    assert spec.owner_id == "main-agent"
    assert spec.priority == 40


def test_tool_call_to_action_spec_rejects_bad_arguments() -> None:
    """Bad parameters are rejected before ActionExecutor sees them."""
    registry = create_builtin_registry()

    with pytest.raises(ActionParamError):
        tool_call_to_action_spec(
            ToolCall(name="nod", arguments={"cycles": 20}),
            registry=registry,
            request_id="r1",
            owner_id="main-agent",
        )
