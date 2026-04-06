"""SubscribePR — record intent to subscribe to PR events (host must wire webhooks)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..kairos.inbox import record_pr_subscribe_intent
from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)


class SubscribePRTool(Tool):
    """Stub for Claude Code's GitHub PR webhook subscription.

    Records intent to ``kairos_inbox/subscribe_pr.jsonl``. A full implementation
    would register webhooks using ``GITHUB_TOKEN`` / ``GH_TOKEN`` in the host.
    """

    name = "SubscribePR"
    description = (
        "Record that this session wants GitHub pull-request notifications for a "
        "repository. The host must register webhooks separately; this tool only "
        "logs intent unless the runtime implements the GitHub API."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "owner/repo (e.g. org/repo).",
                },
                "events": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GitHub event types to subscribe to (e.g. pull_request).",
                },
            },
            "required": ["repository"],
        }

    async def execute(
        self,
        *,
        context: ToolUseContext,
        repository: str = "",
        events: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        repo = (repository or "").strip()
        if not repo or "/" not in repo:
            return json.dumps(
                {"ok": False, "error": "repository must be owner/repo"},
                ensure_ascii=False,
            )

        ev = list(events or ["pull_request"])
        conv = context.conversation_id or "default"
        record_pr_subscribe_intent(
            conversation_id=conv,
            repository=repo,
            events=ev,
        )
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        hint = (
            "Recorded subscribe intent. Set GITHUB_TOKEN (or GH_TOKEN) and register "
            "webhooks in the host to receive live PR events."
            if not token
            else "Recorded subscribe intent; host should register webhooks for this repo."
        )
        logger.info("SubscribePR intent: %s %s", repo, ev)
        return json.dumps(
            {"ok": True, "repository": repo, "events": ev, "hint": hint},
            ensure_ascii=False,
        )
