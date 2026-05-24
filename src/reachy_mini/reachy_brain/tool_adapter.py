"""Adapters between ActionRegistry metadata and Brain tool calls."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from reachy_mini.action_runtime import ActionParamError, ActionSpec
from reachy_mini.action_runtime.registry import ActionRegistry


@dataclass(frozen=True)
class ToolDefinition:
    """LLM-safe action tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCall:
    """Normalized model tool call."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def action_tools(registry: ActionRegistry) -> list[ToolDefinition]:
    """Expose registered actions as LLM-safe tool definitions."""
    return [
        ToolDefinition(
            name=metadata.name,
            description=metadata.description,
            input_schema=metadata.parameter_schema,
            metadata={"tags": list(metadata.tags)},
        )
        for metadata in registry.list_metadata()
    ]


def tool_call_to_action_spec(
    call: ToolCall,
    *,
    registry: ActionRegistry,
    request_id: str,
    owner_id: str,
    parent_request_id: str | None = None,
) -> ActionSpec:
    """Convert a tool call to a validated ActionSpec without executing it."""
    metadata = registry.get_metadata(call.name)
    spec = ActionSpec(
        name=call.name,
        params=dict(call.arguments),
        reason=call.reason,
        request_id=request_id or f"brain_{uuid.uuid4().hex}",
        owner_id=owner_id,
        priority=metadata.default_priority,
        interruptible=metadata.default_interruptible,
        deadline_s=metadata.default_duration_s,
        parent_request_id=parent_request_id,
    )
    try:
        registry.build(spec)
    except ActionParamError:
        raise
    return spec
