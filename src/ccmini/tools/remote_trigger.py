"""Remote trigger tool built on the polling bridge client."""

from __future__ import annotations

import json
from typing import Any

from ..bridge import (
    BridgeMessage,
    MessageType,
    RemoteBridgeSession,
    RemoteBridgeSessionConfig,
    create_bridge_transport,
)
from ..tool import Tool, ToolUseContext


class RemoteTriggerTool(Tool):
    """Trigger remote bridge actions without exposing credentials to shell."""

    name = "RemoteTrigger"
    description = "Manage or run remote bridge sessions and triggers."
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "events", "query", "tool_call"],
                    "description": "Remote trigger action to perform.",
                },
                "base_url": {"type": "string", "description": "Bridge base URL, e.g. http://127.0.0.1:7779"},
                "auth_token": {"type": "string", "description": "Bridge bearer token."},
                "session_id": {"type": "string", "description": "Remote bridge session ID."},
                "transport_mode": {
                    "type": "string",
                    "description": "Transport mode: auto, polling, sse, ws.",
                    "default": "auto",
                },
                "text": {"type": "string", "description": "Query text for action=query."},
                "tool_name": {"type": "string", "description": "Tool name for action=tool_call."},
                "tool_input": {"type": "object", "description": "Tool input for action=tool_call."},
                "since": {"type": "integer", "description": "Replay events after this sequence number.", "default": 0},
                "limit": {"type": "integer", "description": "Maximum replay events.", "default": 100},
            },
            "required": ["action", "base_url", "auth_token", "session_id"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        action = kwargs["action"]
        session = RemoteBridgeSession(
            RemoteBridgeSessionConfig(
                base_url=kwargs["base_url"],
                auth_token=kwargs["auth_token"],
                session_id=kwargs["session_id"],
                mode=str(kwargs.get("transport_mode", "auto")),
            )
        )

        if action == "status":
            transport = session.transport
            if hasattr(transport, "connect"):
                await transport.connect()
            return json.dumps(
                {
                    "state": transport.state.value,
                    "last_sequence_num": getattr(transport, "last_sequence_num", 0),
                    "reconnect_attempts": transport.stats.reconnect_attempts,
                    "last_error": transport.stats.last_error,
                },
                indent=2,
                ensure_ascii=False,
            )

        if action == "events":
            transport = session.transport
            if hasattr(transport, "_last_sequence_num"):
                transport._last_sequence_num = int(kwargs.get("since", 0) or 0)
            if hasattr(transport, "poll_events"):
                events = await transport.poll_events(limit=int(kwargs.get("limit", 100) or 100))
            else:
                raise RuntimeError("Selected transport does not support explicit event polling")
            return json.dumps({"events": events}, indent=2, ensure_ascii=False)

        if action == "query":
            response = await session.query(kwargs.get("text", ""))
            return json.dumps(
                {
                    "type": response.type.value,
                    "payload": response.payload,
                    "sequence_num": response.sequence_num,
                },
                indent=2,
                ensure_ascii=False,
            )

        if action == "tool_call":
            response = await session.tool_call(
                kwargs.get("tool_name", ""),
                kwargs.get("tool_input", {}) or {},
            )
            return json.dumps(
                {
                    "type": response.type.value,
                    "payload": response.payload,
                    "sequence_num": response.sequence_num,
                },
                indent=2,
                ensure_ascii=False,
            )

        return f"Unsupported action: {action}"
