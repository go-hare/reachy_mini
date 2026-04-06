"""Tool base class and execution context for the mini-agent engine.

Every tool (built-in, profile-defined, or MCP-bridged) must subclass
:class:`Tool`.  The engine uses ``is_read_only`` to decide whether
the tool can run concurrently with other read-only tools or must be
serialised.
"""

from __future__ import annotations

import inspect
import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Validation result (mirrors TS ValidationResult)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ValidationResult:
    """Result of Tool.validate_input()."""
    result: bool
    message: str = ""
    error_code: int = 0


# ---------------------------------------------------------------------------
# ToolResult (mirrors TS ToolResult<Output>)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ToolResult:
    """Structured result from a tool execution.

    Mirrors TS ``ToolResult<Output>``:
    - ``output``: the content to send back to the model
    - ``output_for_progress_display``: optional shorter version for UI
    - ``behavior``: "output" (default), "error", "deny", "skip"
    - ``context_modifier``: optional callback to modify ToolUseContext for subsequent tools
    """
    output: str | list[Any] = ""
    output_for_progress_display: str | None = None
    behavior: str = "output"  # "output" | "error" | "deny" | "skip"
    context_modifier: Callable[["ToolUseContext"], "ToolUseContext"] | None = None


# ---------------------------------------------------------------------------
# Permission result (mirrors TS PermissionResult)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PermissionResult:
    """Result of Tool.check_permissions()."""
    behavior: str = "allow"          # "allow" | "deny" | "ask"
    updated_input: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Query chain tracking (mirrors TS QueryChainTracking)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class QueryChainTracking:
    chain_id: str = ""
    depth: int = 0


# ---------------------------------------------------------------------------
# ToolUseContext
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ToolUseContext:
    """Runtime context passed to every tool invocation."""
    conversation_id: str = ""
    agent_id: str = ""
    agent_type: str = ""
    turn_id: str = ""
    tool_use_id: str = ""
    messages: list[Any] = field(default_factory=list)
    system_prompt: str | list[dict[str, Any]] | None = None
    read_file_state: dict[str, float] = field(default_factory=dict)
    query_tracking: dict[str, Any] = field(default_factory=dict)
    tool_decisions: dict[str, Any] = field(default_factory=dict)
    turn_state: Any = None
    extras: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    abort_event: Any = None
    set_in_progress_tool_use_ids: Callable[[set[str]], None] | None = None
    set_has_interruptible_tool_in_progress: Callable[[bool], None] | None = None
    set_response_length: Callable[[int], None] | None = None
    user_modified: bool = False
    file_reading_limits: dict[str, Any] | None = None
    glob_limits: dict[str, Any] | None = None
    require_can_use_tool: bool = False
    preserve_tool_use_results: bool = False
    rendered_system_prompt: Any = None
    content_replacement_state: Any = None

    @property
    def query_source(self) -> str:
        value = self.extras.get("query_source")
        if value:
            return str(value)
        value = self.options.get("query_source")
        return str(value) if value else ""

    @property
    def is_non_interactive_session(self) -> bool:
        value = self.extras.get("is_non_interactive")
        if value is not None:
            return bool(value)
        value = self.options.get("is_non_interactive_session")
        return bool(value)


def tool_matches_name(tool: "Tool", name: str) -> bool:
    """Return whether *name* matches a tool's primary name or aliases."""
    return tool.name == name or name in getattr(tool, "aliases", ())


def find_tool_by_name(tools: Iterable["Tool"], name: str) -> "Tool | None":
    """Find a tool by primary name or alias."""
    for tool in tools:
        if tool_matches_name(tool, name):
            return tool
    return None


class ToolProgress:
    """A single progress update from a streaming tool.

    Mirrors TS ``ToolProgress<P>`` which carries ``toolUseID`` + ``data``.
    The ``content`` / ``metadata`` aliases are kept for backward compat.
    """
    __slots__ = ("tool_use_id", "data", "content", "metadata")

    def __init__(
        self,
        content: str = "",
        metadata: dict[str, Any] | None = None,
        *,
        tool_use_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.metadata = metadata or {}
        self.tool_use_id = tool_use_id
        self.data = data or self.metadata


class Tool(ABC):
    """Abstract base for all tools."""

    name: str = ""
    aliases: tuple[str, ...] = ()
    description: str = ""
    instructions: str = ""
    is_read_only: bool = False
    supports_streaming: bool = False

    # --- TS-parity attributes (class-level defaults) ---
    search_hint: str = ""
    is_mcp: bool = False
    is_lsp: bool = False
    should_defer: bool = False
    always_load: bool = False
    mcp_info: dict[str, str] | None = None
    max_result_size_chars: int = 0  # 0 means no limit; TS uses Infinity for some tools
    input_json_schema: dict[str, Any] | None = None
    strict: bool = False

    @abstractmethod
    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str | ToolResult:
        """Run the tool and return a plain-text result or structured ToolResult.

        Returning a ``ToolResult`` allows tools to specify behavior
        (error, deny, skip) and context modifiers, matching TS semantics.
        Returning a plain ``str`` is equivalent to ``ToolResult(output=str)``.
        """

    async def stream_execute(
        self, *, context: ToolUseContext, **kwargs: Any
    ) -> AsyncIterator[ToolProgress | str]:
        """Stream progress updates, then yield the final result string.

        Override this for tools that can report incremental progress
        (e.g. bash commands streaming stdout). The last yielded value
        MUST be a ``str`` (the final result). All preceding values
        should be ``ToolProgress`` instances.

        Default: falls back to ``execute()`` (no streaming).
        """
        result = await self.execute(context=context, **kwargs)
        yield result

    def is_enabled(self) -> bool:
        """Whether the tool is available for use."""
        return True

    def inputs_equivalent(
        self,
        left: dict[str, Any] | None,
        right: dict[str, Any] | None,
    ) -> bool:
        """Whether two tool inputs should be treated as equivalent."""
        return left == right

    def is_concurrency_safe(self, input_data: dict[str, Any] | None = None) -> bool:
        """Whether multiple calls may run in parallel safely.

        TS default (fail-closed): ``false``.  Override in read-only tools.
        """
        del input_data
        return False

    def is_read_only_call(self, input_data: dict[str, Any] | None = None) -> bool:
        """Call-form alias for the reference ``isReadOnly(input)`` contract."""
        del input_data
        return bool(self.is_read_only)

    def is_destructive(self, input_data: dict[str, Any] | None = None) -> bool:
        """Whether a tool performs an irreversible action."""
        del input_data
        return False

    def user_facing_name(self, input_data: dict[str, Any] | None = None) -> str:
        """Human-facing tool name."""
        del input_data
        return self.name

    def interrupt_behavior(self) -> str:
        """How the tool behaves when the user interrupts a running turn.

        Returns ``'cancel'`` or ``'block'``.  Defaults to ``'block'``.
        """
        return "block"

    # --- TS-parity methods (defaults match buildTool / TOOL_DEFAULTS) ---

    async def validate_input(
        self,
        input_data: dict[str, Any],
        context: ToolUseContext,
    ) -> ValidationResult:
        """Validate tool input before execution.  Default: always valid."""
        return ValidationResult(result=True)

    async def check_permissions(
        self,
        input_data: dict[str, Any],
        context: ToolUseContext,
    ) -> PermissionResult:
        """Check tool-specific permissions.  Default: allow with unchanged input."""
        return PermissionResult(behavior="allow", updated_input=input_data)

    def get_path(self, input_data: dict[str, Any]) -> str | None:
        """Return the file path this tool operates on, if any."""
        return None

    def to_auto_classifier_input(self, input_data: dict[str, Any]) -> Any:
        """Compact representation for the auto-mode security classifier.

        Default: ``''`` (skip — security-relevant tools must override).
        """
        return ""

    def get_tool_use_summary(self, input_data: dict[str, Any] | None = None) -> str | None:
        """Short string summary for compact views.  ``None`` = don't display."""
        return None

    def get_activity_description(self, input_data: dict[str, Any] | None = None) -> str | None:
        """Present-tense activity description for spinner display."""
        return None

    def is_search_or_read_command(self, input_data: dict[str, Any]) -> dict[str, bool]:
        """Whether this tool use is a search/read/list operation.

        Returns ``{"is_search": False, "is_read": False, "is_list": False}``
        by default.
        """
        return {"is_search": False, "is_read": False, "is_list": False}

    def is_open_world(self, input_data: dict[str, Any] | None = None) -> bool:
        """Whether the tool accesses open-world resources."""
        return False

    def requires_user_interaction(self) -> bool:
        """Whether the tool requires interactive user input."""
        return False

    def is_transparent_wrapper(self) -> bool:
        """Whether the tool is a transparent wrapper that delegates rendering."""
        return False

    async def prepare_permission_matcher(
        self,
        input_data: dict[str, Any],
    ) -> Callable[[str], bool] | None:
        """Prepare a matcher for hook ``if`` conditions.

        Returns a closure ``(pattern: str) -> bool`` or ``None``.
        """
        return None

    @property
    def input_schema(self) -> dict[str, Any]:
        """Schema alias matching the reference Tool contract."""
        return self.get_parameters_schema()

    def get_parameters_schema(self) -> dict[str, Any]:
        """JSON Schema for the tool's input parameters.

        Override this to provide a custom schema.  The default uses
        ``execute``'s type-hints to build a minimal schema.
        """
        sig = inspect.signature(self.execute)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "context", "kwargs"):
                continue
            prop: dict[str, Any] = {}
            annotation = param.annotation
            if annotation is inspect.Parameter.empty:
                prop["type"] = "string"
            elif annotation is str:
                prop["type"] = "string"
            elif annotation is int:
                prop["type"] = "integer"
            elif annotation is float:
                prop["type"] = "number"
            elif annotation is bool:
                prop["type"] = "boolean"
            else:
                prop["type"] = "string"

            if param.default is inspect.Parameter.empty:
                required.append(param_name)

            properties[param_name] = prop

        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def to_api_schema(self) -> dict[str, Any]:
        """Produce the tool description sent to the LLM API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.get_parameters_schema(),
        }


class FunctionTool(Tool):
    """Wrap a plain async/sync callable as a Tool."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        func: Any,
        parameters: dict[str, Any] | None = None,
        is_read_only: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self._func = func
        self._parameters = parameters
        self.is_read_only = is_read_only

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        result = self._func(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)

    def get_parameters_schema(self) -> dict[str, Any]:
        if self._parameters is not None:
            return self._parameters
        return super().get_parameters_schema()


class ClientTool(Tool):
    """A tool whose execution is delegated to the host (client-side).

    The query loop will yield a :class:`PendingToolCallEvent` and wait
    for the host to submit results via ``Agent.submit_tool_results``.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self._parameters = parameters or {"type": "object", "properties": {}}
        self.is_read_only = False

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        raise RuntimeError(
            f"ClientTool '{self.name}' must not be executed server-side. "
            "The host should handle PendingToolCallEvent instead."
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return self._parameters


def get_working_directory(context: ToolUseContext) -> str:
    """Return an agent-scoped working directory override when present."""
    extras = getattr(context, "extras", {}) or {}
    return str(extras.get("working_directory", "") or "").strip()


def resolve_path(path: str, context: ToolUseContext) -> Path:
    """Resolve *path* against the agent-scoped working directory when needed."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    working_directory = get_working_directory(context)
    if working_directory:
        return (Path(working_directory) / candidate).resolve()
    return candidate.resolve()


# ---------------------------------------------------------------------------
# build_tool — mirrors TS ``buildTool`` with TOOL_DEFAULTS
# ---------------------------------------------------------------------------

# Keys that build_tool supplies defaults for (mirrors TS DefaultableToolKeys).
_DEFAULTABLE_KEYS: dict[str, Any] = {
    "is_enabled": lambda self: True,
    "is_concurrency_safe": lambda self, input_data=None: False,
    "is_read_only_call": lambda self, input_data=None: False,
    "is_destructive": lambda self, input_data=None: False,
    "check_permissions": None,  # async — handled specially
    "to_auto_classifier_input": lambda self, input_data=None: "",
    "user_facing_name": None,  # needs self.name — handled specially
}


def build_tool(tool: Tool) -> Tool:
    """Fill in safe defaults on *tool* for any method the subclass didn't override.

    Mirrors TS ``buildTool({ ...TOOL_DEFAULTS, userFacingName: () => def.name, ...def })``.

    Defaults (fail-closed where it matters):
    - ``is_enabled``           → ``True``
    - ``is_concurrency_safe``  → ``False`` (assume not safe)
    - ``is_read_only_call``    → ``False`` (assume writes)
    - ``is_destructive``       → ``False``
    - ``check_permissions``    → allow with unchanged input
    - ``to_auto_classifier_input`` → ``''`` (skip classifier)
    - ``user_facing_name``     → ``tool.name``
    """
    # user_facing_name: if the subclass didn't override, ensure it returns self.name
    base_ufn = Tool.user_facing_name
    if type(tool).user_facing_name is base_ufn:
        # Already returns self.name by default — nothing to patch.
        pass

    return tool
