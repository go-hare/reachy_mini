"""Structured output tool for non-interactive and bridge-driven workflows."""

from __future__ import annotations

import json
from typing import Any

from ..tool import Tool, ToolUseContext


class SyntheticOutputTool(Tool):
    """Return a final structured payload that matches a provided schema."""

    name = "StructuredOutput"
    description = "Return the final response as structured JSON."
    is_read_only = True

    def __init__(self, schema: dict[str, Any] | None = None) -> None:
        self._schema = schema or {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "description": "Structured payload to return.",
                    "additionalProperties": True,
                },
            },
            "required": ["data"],
        }

    def get_parameters_schema(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        return json.dumps(
            {
                "data": "Structured output provided successfully",
                "structured_output": kwargs,
            },
            indent=2,
            ensure_ascii=False,
        )


def create_synthetic_output_tool(schema: dict[str, Any]) -> SyntheticOutputTool:
    """Create a structured-output tool from a JSON schema."""
    return SyntheticOutputTool(schema=schema)
