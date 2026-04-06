"""Sleep tool for Kairos/proactive mode.

Minimal Python analogue of Claude Code's SleepTool: when there is nothing
useful to do, the agent can explicitly sleep until a wake trigger arrives
or the timeout expires.
"""

from __future__ import annotations

from typing import Any

from ..kairos.sleep import sleep_until_wake
from ..tool import Tool, ToolUseContext


class SleepTool(Tool):
    name = "Sleep"
    description = (
        "Sleep until a wake trigger arrives or a timeout expires. "
        "Use when there is no useful action to take right now."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "duration_seconds": {
                    "type": "number",
                    "description": "Requested sleep duration in seconds.",
                    "default": 30,
                },
            },
        }

    async def execute(
        self,
        *,
        context: ToolUseContext,
        duration_seconds: float = 30.0,
        **kwargs: Any,
    ) -> str:
        result = await sleep_until_wake(duration_seconds)
        return (
            f"Slept for {result.slept_for_s:.1f}s. "
            f"Woke because: {result.reason.value}. "
            f"Pending commands: {result.pending_commands}."
        )
