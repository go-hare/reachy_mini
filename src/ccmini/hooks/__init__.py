"""Hook protocols for the query loop and resident mode.

Hooks allow the host application to inject behavior at key points
without modifying the core engine.  Mirrors Claude Code's hook system:

- **PreQueryHook** / **PostQueryHook** — before/after each user turn
- **OnStreamEventHook** — transform or filter every streaming event
- **PreToolUseHook** / **PostToolUseHook** — intercept tool calls/results
- **SessionStartHook** / **SessionEndHook** — lifecycle boundaries
- **StopHook** — custom stop-condition logic
- **NotificationHook** — receive non-interactive notifications
- **IdleHook** — periodic callbacks during idle in resident mode
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from ..messages import Message, StreamEvent, ToolCallEvent, ToolResultEvent

if TYPE_CHECKING:
    from ..agent import Agent


# ── Hook event enum ─────────────────────────────────────────────────

class HookEvent(str, Enum):
    PRE_QUERY = "pre_query"
    POST_QUERY = "post_query"
    STREAM_EVENT = "stream_event"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    STOP = "stop"
    NOTIFICATION = "notification"
    IDLE = "idle"


# ── Decision types ──────────────────────────────────────────────────

class ToolUseDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    MODIFY = "modify"


@dataclass
class PreToolUseResult:
    """Result from a PreToolUseHook."""
    decision: ToolUseDecision = ToolUseDecision.ALLOW
    reason: str = ""
    modified_input: dict[str, Any] | None = None


# ── Existing hook types (unchanged interface) ───────────────────────

class PreQueryHook(ABC):
    """Called before each query() invocation."""

    @abstractmethod
    async def on_pre_query(
        self,
        *,
        user_text: str,
        messages: list[Message],
        agent: Agent,
    ) -> None: ...


class PostQueryHook(ABC):
    """Called after each query() completes."""

    @abstractmethod
    async def on_post_query(
        self,
        *,
        user_text: str,
        reply: str,
        agent: Agent,
    ) -> None: ...


class OnStreamEventHook(ABC):
    """Called for each stream event during query()."""

    @abstractmethod
    async def on_stream_event(
        self,
        event: StreamEvent,
        *,
        agent: Agent,
    ) -> StreamEvent | None:
        """Return the event (possibly modified) or None to suppress it."""
        ...


# ── New hook types ──────────────────────────────────────────────────

class PreToolUseHook(ABC):
    """Called before a tool is executed.

    Can allow, deny, or modify the tool call.
    Optional ``matcher`` limits which tools this hook fires for.
    """

    matcher: str = ""  # glob pattern for tool names; empty = all tools

    @abstractmethod
    async def on_pre_tool_use(
        self,
        event: ToolCallEvent,
        *,
        agent: Agent,
    ) -> PreToolUseResult: ...


class PostToolUseHook(ABC):
    """Called after a tool completes execution."""

    matcher: str = ""

    @abstractmethod
    async def on_post_tool_use(
        self,
        call: ToolCallEvent,
        result: ToolResultEvent,
        *,
        agent: Agent,
    ) -> ToolResultEvent | None:
        """Return the result (possibly modified) or None to keep as-is."""
        ...


class SessionStartHook(ABC):
    """Called when an agent session starts."""

    @abstractmethod
    async def on_session_start(self, *, agent: Agent) -> None: ...


class SessionEndHook(ABC):
    """Called when an agent session ends."""

    @abstractmethod
    async def on_session_end(self, *, agent: Agent) -> None: ...


@dataclass
class StopHookResult:
    """Result from a StopHook — mirrors TS ``{ blockingErrors, preventContinuation }``."""
    blocking_errors: list[str] = field(default_factory=list)
    prevent_continuation: bool = False

    @property
    def should_stop(self) -> bool:
        return self.prevent_continuation or bool(self.blocking_errors)


class StopHook(ABC):
    """Custom stop-condition hook.

    Evaluated after each assistant turn. Returns a StopHookResult with:
    - blocking_errors: error messages to inject as user messages for retry
    - prevent_continuation: if True, stop the query loop immediately

    For backwards compatibility, returning a plain bool is also accepted
    (True → prevent_continuation=True).
    """

    @abstractmethod
    async def should_stop(
        self,
        *,
        reply_text: str,
        turn: int,
        agent: Agent,
    ) -> StopHookResult | bool: ...


class NotificationHook(ABC):
    """Receives non-interactive notifications (warnings, info, errors)."""

    @abstractmethod
    async def on_notification(
        self,
        *,
        level: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None: ...


# ── IdleAction + IdleHook (unchanged) ──────────────────────────────

@dataclass
class IdleAction:
    """Returned by an idle hook to request an action."""
    type: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)


class IdleHook:
    """Periodic callback during resident mode idle periods."""

    def __init__(
        self,
        *,
        interval: float = 15.0,
        callback: Callable[[Agent], Coroutine[Any, Any, IdleAction | None]] | None = None,
    ) -> None:
        self.interval = interval
        self.callback = callback

    async def on_idle(self, agent: Agent) -> IdleAction | None:
        if self.callback is not None:
            return await self.callback(agent)
        return None


# ── Post-sampling hooks ─────────────────────────────────────────────

@dataclass
class PostSamplingContext:
    """Context passed to post-sampling hooks after each assistant response."""
    messages: list[Message]
    system_prompt: str
    reply_text: str
    tool_calls: list[ToolCallEvent] = field(default_factory=list)
    query_source: str = "main"


class PostSamplingHook(ABC):
    """Fires after the model produces a response (fire-and-forget).

    Use for side-effects like memory extraction, analytics, or
    skill improvement — anything that should not block the query loop.
    """

    @abstractmethod
    async def on_post_sampling(
        self,
        context: PostSamplingContext,
        *,
        agent: Agent,
    ) -> None: ...


# ── Union type ──────────────────────────────────────────────────────

Hook = (
    PreQueryHook
    | PostQueryHook
    | OnStreamEventHook
    | PreToolUseHook
    | PostToolUseHook
    | SessionStartHook
    | SessionEndHook
    | StopHook
    | NotificationHook
    | IdleHook
    | PostSamplingHook
)

# ── User hook scripts (re-export) ───────────────────────────────────

from .user_scripts import (
    HookEvent as UserHookEvent,
    HookInput,
    HookOutput,
    HookSpec,
    HookType,
    HookMatcher,
    UserHookRunner,
    execute_hook,
    is_ssrf_safe,
    load_hook_config,
    DiscoveredScript,
    ScriptSandbox,
    ScriptHotReloader,
    discover_user_hooks,
)


# ── Convenience registration decorators ─────────────────────────────
#
# These let callers define hooks as plain async functions:
#
#     @register_pre_query_hook
#     async def my_pre_hook(*, user_text, messages, agent):
#         ...
#
# Each decorator returns a concrete Hook subclass instance.

def register_pre_query_hook(
    fn: Callable[..., Coroutine[Any, Any, None]],
) -> PreQueryHook:
    """Wrap an async function as a :class:`PreQueryHook`."""

    class _Wrapper(PreQueryHook):
        async def on_pre_query(self, *, user_text: str, messages: list[Message], agent: Any) -> None:
            await fn(user_text=user_text, messages=messages, agent=agent)

    _Wrapper.__name__ = _Wrapper.__qualname__ = fn.__name__
    return _Wrapper()


def register_post_query_hook(
    fn: Callable[..., Coroutine[Any, Any, None]],
) -> PostQueryHook:
    """Wrap an async function as a :class:`PostQueryHook`."""

    class _Wrapper(PostQueryHook):
        async def on_post_query(self, *, user_text: str, reply: str, agent: Any) -> None:
            await fn(user_text=user_text, reply=reply, agent=agent)

    _Wrapper.__name__ = _Wrapper.__qualname__ = fn.__name__
    return _Wrapper()


def register_stream_hook(
    fn: Callable[..., Coroutine[Any, Any, "StreamEvent | None"]],
) -> OnStreamEventHook:
    """Wrap an async function as an :class:`OnStreamEventHook`."""

    class _Wrapper(OnStreamEventHook):
        async def on_stream_event(self, event: StreamEvent, *, agent: Any) -> "StreamEvent | None":
            return await fn(event, agent=agent)

    _Wrapper.__name__ = _Wrapper.__qualname__ = fn.__name__
    return _Wrapper()
