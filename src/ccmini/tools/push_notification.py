"""PushNotification — enqueue a user-visible notification (Kairos)."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..kairos.inbox import record_push_notification
from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)


class PushNotificationTool(Tool):
    """Record a push-style notification for the host shell or mobile bridge."""

    name = "PushNotification"
    description = (
        "Enqueue a short notification for the user (Kairos). Use for time-sensitive "
        "alerts when inline chat text might be missed. Does not send a real mobile "
        "push unless the host integrates with the inbox file."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short title (one line).",
                },
                "body": {
                    "type": "string",
                    "description": "Notification body.",
                },
                "priority": {
                    "type": "string",
                    "description": "low | normal | high",
                    "enum": ["low", "normal", "high"],
                },
            },
            "required": ["title", "body"],
        }

    async def execute(
        self,
        *,
        context: ToolUseContext,
        title: str = "",
        body: str = "",
        priority: str = "normal",
        **kwargs: Any,
    ) -> str:
        t = (title or "").strip()
        b = (body or "").strip()
        if not t or not b:
            return json.dumps({"ok": False, "error": "title and body are required"}, ensure_ascii=False)

        pr = (priority or "normal").lower()
        if pr not in ("low", "normal", "high"):
            pr = "normal"

        conv = context.conversation_id or "default"
        record_push_notification(
            conversation_id=conv,
            title=t,
            body=b,
            priority=pr,
        )
        logger.info("PushNotification recorded: %s", t[:80])
        return json.dumps({"ok": True, "title": t, "priority": pr}, ensure_ascii=False)
