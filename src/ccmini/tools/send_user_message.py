"""SendUserMessage tool for brief/chat-mode user-facing updates."""

from __future__ import annotations

import json
from typing import Any

from ..kairos.brief import send_user_message
from ..tool import Tool, ToolUseContext


class SendUserMessageTool(Tool):
    """Record a user-visible message for brief/chat mode."""

    name = "SendUserMessage"
    aliases = ("Brief",)
    description = (
        "Send a concise user-facing update that appears in brief/chat mode. "
        "Use this instead of inline text when brief mode is active."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "User-facing message content.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional short heading for the message.",
                },
            },
            "required": ["content"],
        }

    async def execute(
        self,
        *,
        context: ToolUseContext,
        content: str = "",
        title: str = "",
        **kwargs: Any,
    ) -> str:
        msg = send_user_message(content, title=title)
        return json.dumps(
            {
                "title": msg.title,
                "content": msg.content,
                "timestamp": msg.timestamp,
            },
            ensure_ascii=False,
        )
