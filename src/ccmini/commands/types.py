"""Unified Command type system.

Ported from Claude Code's ``types/command.ts`` — provides a single ``Command``
type that encompasses slash commands, prompt/skill commands, and MCP commands.

Command sources (where it was defined):
- ``builtin``  — shipped with mini_agent (``/clear``, ``/help``, …)
- ``bundled``  — bundled skills (verify, debug, stuck, …)
- ``skills``   — user's ``.mini_agent/skills/`` directory
- ``plugin``   — third-party plugin commands
- ``mcp``      — MCP server-provided commands/skills

The ``loaded_from`` field tracks which loader resolved the command (may differ
from ``source`` in edge cases like bundled skills loaded from disk cache).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable


class CommandType(str, Enum):
    LOCAL = "local"
    PROMPT = "prompt"


class CommandSource(str, Enum):
    BUILTIN = "builtin"
    BUNDLED = "bundled"
    SKILLS = "skills"
    PLUGIN = "plugin"
    MCP = "mcp"


@dataclass
class Command:
    """Unified command / skill descriptor.

    Combines Claude Code's ``CommandBase``, ``PromptCommand``, and ``LocalCommand``
    into a single Python dataclass.
    """
    name: str
    description: str
    type: CommandType = CommandType.LOCAL

    # Identity
    aliases: list[str] = field(default_factory=list)
    source: CommandSource = CommandSource.BUILTIN
    loaded_from: CommandSource | None = None

    # Prompt-type fields (skills)
    prompt_text: str = ""
    content_length: int = 0
    progress_message: str = "running"
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""
    when_to_use: str = ""
    argument_hint: str = ""
    arg_names: list[str] = field(default_factory=list)

    # Execution context
    context: str = "inline"  # "inline" | "fork"
    agent: str = ""
    effort: str = ""
    paths: list[str] = field(default_factory=list)
    hooks: dict[str, str] = field(default_factory=dict)
    skill_root: str = ""

    # Visibility / invocability
    is_enabled: Callable[[], bool] | None = None
    is_hidden: bool = False
    disable_model_invocation: bool = False
    user_invocable: bool = True
    has_user_specified_description: bool = False
    version: str = ""
    priority: int = 0

    # MCP metadata
    is_mcp: bool = False
    mcp_server_name: str = ""

    # Plugin metadata
    plugin_name: str = ""

    @property
    def enabled(self) -> bool:
        if self.is_enabled is not None:
            return self.is_enabled()
        return True

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def effective_loaded_from(self) -> CommandSource:
        return self.loaded_from or self.source

    def format_description_with_source(self) -> str:
        """Format description with source tag for display."""
        if self.source == CommandSource.BUILTIN:
            return self.description
        if self.source == CommandSource.MCP:
            return self.description
        if self.source == CommandSource.BUNDLED:
            return f"{self.description} (bundled)"
        if self.source == CommandSource.PLUGIN and self.plugin_name:
            return f"({self.plugin_name}) {self.description}"
        if self.source == CommandSource.PLUGIN:
            return f"{self.description} (plugin)"
        if self.source == CommandSource.SKILLS:
            return f"{self.description} (skill)"
        return self.description


@runtime_checkable
class CommandExecutor(Protocol):
    """Protocol for executing a local command."""
    async def __call__(self, args: str, context: Any) -> str: ...


@runtime_checkable
class PromptProvider(Protocol):
    """Protocol for getting prompt content from a prompt command."""
    async def __call__(self, args: str, context: Any) -> list[dict[str, str]]: ...
