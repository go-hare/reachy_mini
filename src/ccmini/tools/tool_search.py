"""ToolSearch — lazy tool loading via search.

Ported from Claude Code's ``ToolSearchTool``:
- When the tool pool is large, only tool names are shown in the prompt
- The model uses ToolSearch to fetch full schemas for tools it needs
- Supports ``select:ToolA,ToolB`` for direct selection and keyword search
- Returns tool schemas so the model can invoke them

This prevents the tool definitions from eating thousands of tokens
when many tools (especially MCP tools) are registered.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────

@dataclass(slots=True)
class ToolSearchConfig:
    """Configuration for deferred tool loading."""

    enabled: bool = True
    defer_threshold: int = 15
    always_load_tools: frozenset[str] = frozenset({
        "ToolSearch",
        "Read",
        "Write",
        "Edit",
        "Bash",
        "Glob",
        "Grep",
        "TodoWrite",
    })
    max_search_results: int = 5


DEFAULT_CONFIG = ToolSearchConfig()


# ── Deferred tool management ───────────────────────────────────────

def is_deferred_tool(
    tool: Tool,
    config: ToolSearchConfig = DEFAULT_CONFIG,
) -> bool:
    """Check if a tool should be deferred (schema hidden until searched).

    A tool is deferred when:
    - ToolSearch is enabled
    - Total tool count exceeds the threshold
    - The tool is NOT in the always-load set
    - The tool does not have ``always_load=True`` attribute
    """
    if not config.enabled:
        return False

    if tool.name in config.always_load_tools:
        return False

    if getattr(tool, "always_load", False):
        return False

    if tool.name == "ToolSearch":
        return False

    return True


def partition_tools(
    tools: list[Tool],
    config: ToolSearchConfig = DEFAULT_CONFIG,
) -> tuple[list[Tool], list[Tool]]:
    """Partition tools into loaded and deferred sets.

    Returns ``(loaded_tools, deferred_tools)``.
    If total tools <= threshold, all are loaded (no deferral).
    """
    if not config.enabled or len(tools) <= config.defer_threshold:
        return tools, []

    loaded: list[Tool] = []
    deferred: list[Tool] = []

    for tool in tools:
        if is_deferred_tool(tool, config):
            deferred.append(tool)
        else:
            loaded.append(tool)

    return loaded, deferred


def format_deferred_tool_list(deferred: list[Tool]) -> str:
    """Format deferred tool names for injection into the prompt.

    The model sees this list and can use ToolSearch to fetch full schemas.
    """
    if not deferred:
        return ""

    lines = [t.name for t in deferred]
    return (
        "<available-deferred-tools>\n"
        + "\n".join(lines)
        + "\n</available-deferred-tools>"
    )


# ── Search logic ────────────────────────────────────────────────────

def _parse_tool_name(name: str) -> list[str]:
    """Split a tool name into searchable parts.

    Handles MCP tools (mcp__server__action) and CamelCase.
    """
    if name.startswith("mcp__"):
        return name.replace("mcp__", "").replace("__", " ").replace("_", " ").lower().split()

    # CamelCase → words
    parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return parts.replace("_", " ").lower().split()


def search_tools(
    query: str,
    deferred: list[Tool],
    all_tools: list[Tool],
    *,
    max_results: int = 5,
) -> list[Tool]:
    """Search for tools matching a query.

    Query forms:
    - ``select:Read,Edit,Grep`` — fetch exact tools by name
    - ``notebook jupyter`` — keyword search
    - ``+slack send`` — require "slack" in name, rank by remaining terms
    """
    query = query.strip()

    # Handle select: prefix
    select_match = re.match(r"^select:(.+)$", query, re.IGNORECASE)
    if select_match:
        requested = [n.strip() for n in select_match.group(1).split(",") if n.strip()]
        all_by_name = {t.name.lower(): t for t in all_tools}
        found = []
        for name in requested:
            tool = all_by_name.get(name.lower())
            if tool and tool not in found:
                found.append(tool)
        return found

    # Keyword search
    query_lower = query.lower()
    terms = query_lower.split()

    # Separate required (+prefix) and optional terms
    required = [t[1:] for t in terms if t.startswith("+") and len(t) > 1]
    optional = [t for t in terms if not t.startswith("+")]
    all_scoring = required + optional if required else terms

    # Score each deferred tool
    scored: list[tuple[float, Tool]] = []
    for tool in deferred:
        parts = _parse_tool_name(tool.name)
        name_lower = tool.name.lower()
        desc_lower = (tool.description or "").lower()

        # Required terms must all match
        if required:
            if not all(
                any(r in p for p in parts) or r in desc_lower
                for r in required
            ):
                continue

        score = 0.0
        for term in all_scoring:
            # Exact part match
            if term in parts:
                score += 10
            elif any(term in p for p in parts):
                score += 5
            # Full name match
            elif term in name_lower:
                score += 3
            # Description match
            elif term in desc_lower:
                score += 2

        if score > 0:
            scored.append((score, tool))

    # Also check exact name match across all tools (not just deferred)
    if not scored:
        all_by_name = {t.name.lower(): t for t in all_tools}
        exact = all_by_name.get(query_lower)
        if exact:
            return [exact]

    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:max_results]]


# ── ToolSearch Tool implementation ──────────────────────────────────

class ToolSearchTool(Tool):
    """Model-facing tool that fetches schemas for deferred tools.

    When invoked, it searches the deferred tool pool and returns
    the full schema definitions so the model can call them.
    """

    name = "ToolSearch"
    description = (
        "Fetches full schema definitions for deferred tools so they can be called. "
        "Deferred tools appear by name in <available-deferred-tools> messages. "
        "Use 'select:ToolName' for direct selection or keywords to search."
    )
    is_read_only = True
    always_load = True

    def __init__(
        self,
        all_tools: list[Tool] | None = None,
        deferred_tools: list[Tool] | None = None,
        config: ToolSearchConfig | None = None,
    ) -> None:
        self._all_tools = all_tools or []
        self._deferred = deferred_tools or []
        self._config = config or DEFAULT_CONFIG

    def set_tools(
        self,
        all_tools: list[Tool],
        deferred_tools: list[Tool],
    ) -> None:
        """Update the tool pools (called when tools change dynamically)."""
        self._all_tools = all_tools
        self._deferred = deferred_tools

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        'Query to find deferred tools. '
                        'Use "select:<tool_name>" for direct selection, '
                        'or keywords to search.'
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        *,
        context: ToolUseContext,
        query: str = "",
        max_results: int = 5,
        **kwargs: Any,
    ) -> str:
        """Search for tools and return their schemas."""
        if not query:
            return "Error: query parameter is required"

        matches = search_tools(
            query,
            self._deferred,
            self._all_tools,
            max_results=max_results,
        )

        if not matches:
            return (
                f"No matching deferred tools found for query: {query}\n"
                f"Total deferred tools: {len(self._deferred)}"
            )

        # Format as schema definitions
        parts: list[str] = []
        for tool in matches:
            schema = tool.get_parameters_schema()
            entry = {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": schema,
            }
            import json
            parts.append(json.dumps(entry, indent=2))

        return (
            f"Found {len(matches)} tool(s):\n\n"
            + "\n\n---\n\n".join(parts)
            + f"\n\nTotal deferred tools: {len(self._deferred)}"
        )
