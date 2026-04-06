"""Unified command framework.

Ported from Claude Code's ``commands.ts`` — provides a single registry that
merges slash commands, bundled skills, user skills, plugin commands, and MCP
commands.  Priority ordering (first wins on name conflict):

    bundled → plugin-builtin → skill-dir → plugin → builtin

Public API
----------
- ``CommandRegistry``  — central registry with merge, cache, and lookup
- ``SlashCommand``     — ABC for legacy local slash commands
- ``Command``          — unified descriptor (re-exported from ``types``)
- ``get_commands()``   — all enabled commands, respecting availability
- ``find_command()``   — lookup by name or alias
- ``get_skill_tool_commands()`` — prompt commands the model can invoke
- ``get_slash_command_tool_skills()`` — skill-type commands for /skill
- ``get_mcp_skill_commands()`` — MCP-provided skills
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .types import Command, CommandSource, CommandType

if TYPE_CHECKING:
    from ..agent import Agent

logger = logging.getLogger(__name__)

# ── Legacy SlashCommand ABC (kept for backward compatibility) ────────


class SlashCommand(ABC):
    """Base class for local slash commands."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def description(self) -> str:
        return ""

    @property
    def aliases(self) -> list[str]:
        return []

    @property
    def enabled(self) -> bool:
        return True

    @property
    def hidden(self) -> bool:
        return False

    @abstractmethod
    async def execute(self, args: str, agent: Agent) -> str: ...

    def to_command(self) -> Command:
        """Convert legacy SlashCommand to unified Command."""
        return Command(
            name=self.name,
            description=self.description,
            type=CommandType.LOCAL,
            aliases=list(self.aliases),
            source=CommandSource.BUILTIN,
            loaded_from=CommandSource.BUILTIN,
            is_enabled=lambda: self.enabled,
            is_hidden=self.hidden,
        )


# ── Unified registry ────────────────────────────────────────────────


class CommandRegistry:
    """Central registry for all command types.

    Merges multiple command sources with deterministic priority:
    bundled → plugin-builtin → skill-dir → plugin → builtin

    Caches the merged list and supports invalidation when dynamic
    commands (e.g., MCP skills) are added at runtime.
    """

    def __init__(self) -> None:
        # Legacy slash commands (name → SlashCommand)
        self._slash_commands: dict[str, SlashCommand] = {}
        self._slash_alias_map: dict[str, str] = {}

        # Unified commands by source layer
        self._builtin: list[Command] = []
        self._bundled: list[Command] = []
        self._skills: list[Command] = []
        self._plugin: list[Command] = []
        self._mcp: list[Command] = []
        self._dynamic: list[Command] = []

        # Merged cache
        self._cache: list[Command] | None = None
        self._name_index: dict[str, Command] | None = None

    # ── Registration ─────────────────────────────────────────────

    def register(self, cmd: SlashCommand) -> None:
        """Register a legacy slash command (backward compat)."""
        self._slash_commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._slash_alias_map[alias] = cmd.name
        self._builtin.append(cmd.to_command())
        self._invalidate()

    def register_command(self, cmd: Command) -> None:
        """Register a unified Command into its source layer."""
        layer = self._layer_for(cmd.source)
        layer.append(cmd)
        self._invalidate()

    def register_commands(self, cmds: list[Command]) -> None:
        for cmd in cmds:
            layer = self._layer_for(cmd.source)
            layer.append(cmd)
        if cmds:
            self._invalidate()

    def add_dynamic_command(self, cmd: Command) -> None:
        """Add a command discovered at runtime (e.g., dynamic skill)."""
        self._dynamic.append(cmd)
        self._invalidate()

    def remove_commands_by_source(self, source: CommandSource) -> int:
        """Remove all commands from a specific source. Returns count removed."""
        layer = self._layer_for(source)
        count = len(layer)
        layer.clear()
        if count > 0:
            self._invalidate()
        return count

    def _layer_for(self, source: CommandSource) -> list[Command]:
        mapping = {
            CommandSource.BUILTIN: self._builtin,
            CommandSource.BUNDLED: self._bundled,
            CommandSource.SKILLS: self._skills,
            CommandSource.PLUGIN: self._plugin,
            CommandSource.MCP: self._mcp,
        }
        return mapping.get(source, self._dynamic)

    # ── Lookup ───────────────────────────────────────────────────

    def get(self, name: str) -> SlashCommand | None:
        """Legacy lookup for slash commands."""
        if name in self._slash_commands:
            return self._slash_commands[name]
        canonical = self._slash_alias_map.get(name)
        if canonical:
            return self._slash_commands.get(canonical)
        return None

    def parse(self, text: str) -> tuple[SlashCommand, str] | None:
        """Parse user input. Returns ``(command, args)`` or None."""
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None
        parts = stripped[1:].split(None, 1)
        if not parts:
            return None
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        cmd = self.get(cmd_name)
        if cmd is None or not cmd.enabled:
            return None
        return cmd, args

    def parse_user_command(self, text: str) -> tuple[Command, str] | None:
        """Parse any user-invocable unified command from slash input."""
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None
        parts = stripped[1:].split(None, 1)
        if not parts:
            return None
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        cmd = self.find_command(cmd_name)
        if cmd is None or not cmd.user_invocable or not cmd.enabled:
            return None
        return cmd, args

    def list_commands(self) -> list[SlashCommand]:
        """Legacy: list only slash commands."""
        return [
            cmd for cmd in self._slash_commands.values()
            if cmd.enabled and not cmd.hidden
        ]

    # ── Unified API ──────────────────────────────────────────────

    def get_all_commands(self) -> list[Command]:
        """All enabled commands, merged with priority ordering."""
        if self._cache is not None:
            return self._cache

        merged: list[Command] = []
        seen_names: set[str] = set()

        for layer in (
            self._bundled,
            self._skills,
            self._plugin,
            self._dynamic,
            self._builtin,
            self._mcp,
        ):
            for cmd in layer:
                if not cmd.enabled:
                    continue
                if cmd.name not in seen_names:
                    seen_names.add(cmd.name)
                    merged.append(cmd)
                    for alias in cmd.aliases:
                        seen_names.add(alias)

        self._cache = merged
        self._build_name_index(merged)
        return merged

    def find_command(self, name: str) -> Command | None:
        """Find a command by name or alias."""
        if self._name_index is None:
            self.get_all_commands()
        assert self._name_index is not None
        return self._name_index.get(name)

    def get_by_name(self, name: str) -> Command | None:
        """Backward-compatible alias for unified command lookup."""
        return self.find_command(name)

    def has_command(self, name: str) -> bool:
        return self.find_command(name) is not None

    def get_skill_tool_commands(self) -> list[Command]:
        """Prompt commands the model can invoke via SkillTool.

        Includes skills and bundled commands that have descriptions or
        ``when_to_use`` set, and are not disabled for model invocation.
        """
        return [
            cmd for cmd in self.get_all_commands()
            if (
                cmd.type == CommandType.PROMPT
                and not cmd.disable_model_invocation
                and cmd.source != CommandSource.BUILTIN
                and (
                    cmd.effective_loaded_from in (
                        CommandSource.BUNDLED,
                        CommandSource.SKILLS,
                    )
                    or cmd.has_user_specified_description
                    or cmd.when_to_use
                )
            )
        ]

    def get_slash_command_tool_skills(self) -> list[Command]:
        """Skills that appear in /skill slash-command listings.

        These are prompt-type commands with descriptions that come from
        skills, plugins, or bundled sources.
        """
        return [
            cmd for cmd in self.get_all_commands()
            if (
                cmd.type == CommandType.PROMPT
                and cmd.source != CommandSource.BUILTIN
                and (cmd.has_user_specified_description or cmd.when_to_use)
                and cmd.effective_loaded_from in (
                    CommandSource.SKILLS,
                    CommandSource.PLUGIN,
                    CommandSource.BUNDLED,
                )
            )
        ]

    def get_mcp_skill_commands(self) -> list[Command]:
        """MCP-provided skills the model can invoke."""
        return [
            cmd for cmd in self._mcp
            if (
                cmd.type == CommandType.PROMPT
                and cmd.effective_loaded_from == CommandSource.MCP
                and not cmd.disable_model_invocation
                and cmd.enabled
            )
        ]

    def get_builtin_names(self) -> set[str]:
        """All builtin command names + aliases."""
        names: set[str] = set()
        for cmd in self._builtin:
            names.add(cmd.name)
            names.update(cmd.aliases)
        return names

    # ── Cache management ─────────────────────────────────────────

    def _invalidate(self) -> None:
        self._cache = None
        self._name_index = None

    def _build_name_index(self, commands: list[Command]) -> None:
        idx: dict[str, Command] = {}
        for cmd in commands:
            idx[cmd.name] = cmd
            for alias in cmd.aliases:
                if alias not in idx:
                    idx[alias] = cmd
        self._name_index = idx

    def clear_cache(self) -> None:
        """Fully clear all caches (e.g. after plugin reload)."""
        self._invalidate()

    # ── Helpers ──────────────────────────────────────────────────

    def command_count(self) -> dict[str, int]:
        """Count commands per source layer."""
        return {
            "builtin": len(self._builtin),
            "bundled": len(self._bundled),
            "skills": len(self._skills),
            "plugin": len(self._plugin),
            "mcp": len(self._mcp),
            "dynamic": len(self._dynamic),
        }

    def summary(self) -> str:
        """Human-readable summary of registered commands."""
        counts = self.command_count()
        total = sum(counts.values())
        parts = [f"{k}: {v}" for k, v in counts.items() if v > 0]
        return f"{total} commands ({', '.join(parts)})"


# ── Convenience aliases (backward compat) ────────────────────────────

SlashCommandRegistry = CommandRegistry


# ── Module-level helpers ─────────────────────────────────────────────


def command_from_bundled_skill(skill: Any) -> Command:
    """Convert a BundledSkillDefinition to a unified Command."""
    return Command(
        name=skill.name,
        description=skill.description,
        type=CommandType.PROMPT,
        source=CommandSource.BUNDLED,
        loaded_from=CommandSource.BUNDLED,
        when_to_use=getattr(skill, "when_to_use", ""),
        allowed_tools=skill.allowed_tools or [],
        priority=getattr(skill, "priority", 5),
        disable_model_invocation=False,
        user_invocable=True,
        has_user_specified_description=True,
        prompt_text=skill.prompt,
    )


def command_from_skill(skill: Any) -> Command:
    """Convert a skills.Skill to a unified Command."""
    fm = skill.frontmatter
    return Command(
        name=skill.name,
        description=fm.description or skill.title or skill.name,
        type=CommandType.PROMPT,
        source=CommandSource.SKILLS,
        loaded_from=CommandSource.SKILLS,
        allowed_tools=fm.allowed_tools,
        model=fm.model,
        paths=fm.paths,
        effort=fm.effort,
        context="fork" if fm.agent else "inline",
        agent=fm.agent,
        user_invocable=fm.user_invocable,
        has_user_specified_description=bool(fm.description),
        priority=fm.priority,
        prompt_text=skill.body or skill.content,
        aliases=[],
    )


def command_from_mcp_skill(
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
) -> Command:
    """Create a Command from MCP-discovered skill metadata."""
    return Command(
        name=name,
        description=description,
        type=CommandType.PROMPT,
        source=CommandSource.MCP,
        loaded_from=CommandSource.MCP,
        is_mcp=True,
        mcp_server_name=server_name,
        prompt_text=prompt_text,
        when_to_use=when_to_use,
        allowed_tools=allowed_tools or [],
        model=model,
        paths=paths or [],
        user_invocable=user_invocable,
        disable_model_invocation=False,
        has_user_specified_description=bool(description),
    )


def find_command(name: str, commands: list[Command]) -> Command | None:
    """Find a command by name or alias in a list."""
    for cmd in commands:
        if cmd.name == name or name in cmd.aliases:
            return cmd
    return None


def has_command(name: str, commands: list[Command]) -> bool:
    return find_command(name, commands) is not None
