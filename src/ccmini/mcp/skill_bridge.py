"""MCP Skill Bridge — write-once registry for skill builder functions.

Ported from Claude Code's ``skills/mcpSkillBuilders.ts``.

This module is a **dependency-graph leaf**: it imports nothing except
standard-library types, so both ``mcp/`` and ``skills/`` can depend on
it without forming a circular import.

The pattern:

1. ``skills/__init__.py`` calls ``register_mcp_skill_builders()`` at
   module init with references to ``parse_skill_frontmatter`` and
   ``command_from_mcp_skill``.
2. ``mcp/manager.py`` (or wherever MCP skills are fetched) calls
   ``get_mcp_skill_builders()`` to obtain those functions and use them
   to convert MCP-discovered skill resources into unified ``Command``
   objects.

Registration happens eagerly at import time because ``skills/__init__``
is imported before any MCP server connects.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, TypedDict, runtime_checkable

logger = logging.getLogger(__name__)


# ── Types ────────────────────────────────────────────────────────────

class SkillFrontmatterResult(TypedDict, total=False):
    """Subset of parsed frontmatter relevant to MCP skill creation."""
    description: str
    allowed_tools: list[str]
    model: str
    paths: list[str]
    when_to_use: str
    user_invocable: bool
    tags: list[str]
    priority: int


@runtime_checkable
class FrontmatterParser(Protocol):
    """Protocol matching ``parse_skill_frontmatter``."""
    def __call__(self, text: str) -> tuple[Any, str]: ...


@runtime_checkable
class McpCommandFactory(Protocol):
    """Protocol matching ``command_from_mcp_skill``."""
    def __call__(
        self,
        name: str,
        description: str,
        *,
        server_name: str = "",
        prompt_text: str = "",
        when_to_use: str = "",
        allowed_tools: list[str] | None = None,
        model: str = "",
        paths: list[str] | None = None,
        user_invocable: bool = True,
    ) -> Any: ...


class MCPSkillBuilders:
    """Container for the two functions MCP skill discovery needs."""

    __slots__ = ("parse_frontmatter", "create_command")

    def __init__(
        self,
        parse_frontmatter: FrontmatterParser,
        create_command: McpCommandFactory,
    ) -> None:
        self.parse_frontmatter = parse_frontmatter
        self.create_command = create_command


# ── Global registry ──────────────────────────────────────────────────

_builders: MCPSkillBuilders | None = None


def register_mcp_skill_builders(builders: MCPSkillBuilders) -> None:
    """Register skill builder functions.

    Called once at startup from ``skills/__init__.py`` module init.
    """
    global _builders
    _builders = builders
    logger.debug("MCP skill builders registered")


def get_mcp_skill_builders() -> MCPSkillBuilders:
    """Retrieve registered builders. Raises if not yet registered."""
    if _builders is None:
        raise RuntimeError(
            "MCP skill builders not registered — "
            "skills/__init__.py has not been evaluated yet"
        )
    return _builders


def is_registered() -> bool:
    """Check if builders have been registered."""
    return _builders is not None


# ── High-level helpers ───────────────────────────────────────────────

def build_mcp_skill_command(
    name: str,
    skill_content: str,
    *,
    server_name: str = "",
) -> Any:
    """Parse a SKILL.md-formatted string from an MCP server and create a Command.

    This is the main entry point for MCP skill discovery: given the raw
    content of a ``skill://`` resource, parse its frontmatter and produce
    a unified ``Command``.

    Returns a ``Command`` instance or ``None`` if parsing fails.
    """
    builders = get_mcp_skill_builders()

    try:
        fm, body = builders.parse_frontmatter(skill_content)
    except Exception:
        logger.warning("Failed to parse MCP skill frontmatter for %s", name)
        return None

    description = getattr(fm, "description", "") or name
    allowed_tools = getattr(fm, "allowed_tools", [])
    model = getattr(fm, "model", "")
    paths = getattr(fm, "paths", [])
    user_invocable = getattr(fm, "user_invocable", True)

    return builders.create_command(
        name,
        description,
        server_name=server_name,
        prompt_text=body or skill_content,
        allowed_tools=allowed_tools or None,
        model=model,
        paths=paths or None,
        user_invocable=user_invocable,
    )


def _coerce_skill_resource_text(content: Any) -> str:
    """Normalize MCP resource payloads into SKILL.md text."""
    if isinstance(content, str):
        return content

    if not isinstance(content, dict):
        return ""

    contents = content.get("contents")
    if not isinstance(contents, list):
        return ""

    text_parts: list[str] = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            text_parts.append(text)

    return "\n".join(text_parts)


async def fetch_mcp_skills_for_client(
    client: Any,
    *,
    server_name: str = "",
) -> list[Any]:
    """Discover skill resources from an MCP client and convert to Commands.

    Looks for resources with URI scheme ``skill://`` and attempts to
    read their content as SKILL.md-formatted text.

    Parameters
    ----------
    client:
        An ``McpClient`` instance with ``list_resources()`` and
        ``read_resource()`` methods.
    server_name:
        Name of the MCP server (for metadata tagging).

    Returns
    -------
    list[Command]
        Parsed skill commands, possibly empty.
    """
    if not is_registered():
        return []

    try:
        connection = getattr(client, "connection", None)
        if connection is None:
            return []

        resources = getattr(connection, "resources", [])
        if not resources:
            return []
    except Exception:
        return []

    commands: list[Any] = []

    for resource in resources:
        uri = getattr(resource, "uri", "") or ""
        if not uri.startswith("skill://"):
            continue

        skill_name = uri.removeprefix("skill://").strip("/")
        if not skill_name:
            continue

        try:
            content = await client.read_resource(uri)
            skill_text = _coerce_skill_resource_text(content)
            if not skill_text:
                continue
        except Exception:
            logger.debug("Failed to read MCP skill resource %s", uri)
            continue

        cmd = build_mcp_skill_command(
            skill_name,
            skill_text,
            server_name=server_name or getattr(client, "name", ""),
        )
        if cmd is not None:
            commands.append(cmd)

    if commands:
        logger.info(
            "Discovered %d MCP skills from server %s",
            len(commands),
            server_name,
        )

    return commands
