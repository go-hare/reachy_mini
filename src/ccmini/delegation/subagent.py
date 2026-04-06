"""Sub-agent system: spawn nested agent tasks with isolated context.

Supports two execution modes:
- **sync**: block the parent query loop until the sub-agent finishes
- **async**: run in background, parent continues

Sub-agents reuse the same query() loop but with their own tool set,
prompt, and message history (fork semantics).

Extended features (ported from Claude Code's AgentTool / forkedAgent):
- ForkedAgentContext: shared parent context for prompt-cache-efficient forks
- run_forked_agent(): deep-copy fork with tool filtering and abort support
- Tool permission factories: read-only, edit-only, memory-agent, compact-agent
- AgentLifecycle: start/stop/abort with timeout and callbacks
- AgentSummaryTracker: periodic 3-5 word progress summaries
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from ..messages import (
    CompletionEvent,
    ErrorEvent,
    Message,
    StreamEvent,
    TextBlock,
    ToolResultEvent,
    user_message,
)
from ..prompts import SystemPrompt
from ..providers import BaseProvider
from ..engine.query import QueryParams, query
from ..tool import Tool

logger = logging.getLogger(__name__)


class _SubagentAgentView:
    """Lightweight agent facade for hooks running inside a sub-agent."""

    def __init__(
        self,
        *,
        parent: Any | None,
        conversation_id: str,
        agent_id: str,
        provider: BaseProvider,
        messages: list[Message],
        tools: list[Tool],
        system_prompt: str | SystemPrompt,
    ) -> None:
        self._parent = parent
        self._conversation_id = conversation_id
        self._agent_id = agent_id
        self._provider = provider
        self._messages = messages
        self._tools = tools
        self._system_prompt = system_prompt

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def provider(self) -> BaseProvider:
        return self._provider

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools)

    def __getattr__(self, name: str) -> Any:
        parent = self._parent
        if parent is None:
            raise AttributeError(name)
        return getattr(parent, name)


def _subagent_runtime_overrides(
    *,
    runtime_overrides: dict[str, Any] | None,
    provider: BaseProvider,
    messages: list[Message],
    tools: list[Tool],
    system_prompt: str | SystemPrompt,
    conversation_id: str,
    agent_id: str,
) -> dict[str, Any]:
    overrides = dict(runtime_overrides or {})
    parent_agent = overrides.get("agent")
    overrides["agent"] = _SubagentAgentView(
        parent=parent_agent,
        conversation_id=conversation_id,
        agent_id=agent_id,
        provider=provider,
        messages=messages,
        tools=tools,
        system_prompt=system_prompt,
    )
    return overrides


async def run_subagent(
    *,
    provider: BaseProvider,
    system_prompt: str | SystemPrompt,
    user_text: str = "",
    tools: list[Tool] | None = None,
    parent_messages: list[Message] | None = None,
    initial_messages: list[Message] | None = None,
    max_turns: int = 10,
    agent_id: str = "",
    query_source: str = "",
    runtime_overrides: dict[str, Any] | None = None,
) -> str:
    """Run a sub-agent synchronously and return its final reply.

    The sub-agent gets a fresh message list (fork), optionally seeded
    with context from the parent.
    """
    messages: list[Message] = []
    if parent_messages:
        messages.extend(parent_messages)
    if initial_messages:
        messages.extend(initial_messages)
    elif user_text:
        messages.append(user_message(user_text))

    if isinstance(system_prompt, str):
        sp = SystemPrompt()
        sp.add_static(system_prompt)
    else:
        sp = system_prompt

    conv_id = uuid4().hex[:16]
    subagent_id = agent_id or f"subagent_{uuid4().hex[:8]}"
    effective_overrides = _subagent_runtime_overrides(
        runtime_overrides=runtime_overrides,
        provider=provider,
        messages=messages,
        tools=list(tools or []),
        system_prompt=sp,
        conversation_id=conv_id,
        agent_id=subagent_id,
    )

    params = QueryParams(
        provider=provider,
        system_prompt=sp,
        messages=messages,
        tools=list(tools or []),
        conversation_id=conv_id,
        agent_id=subagent_id,
        max_turns=max_turns,
        query_source=query_source,
        turn_state=SimpleNamespace(
            abort_event=effective_overrides.get("abort_event"),
        ) if effective_overrides.get("abort_event") is not None else None,
    )
    if effective_overrides:
        for key, value in effective_overrides.items():
            if hasattr(params, key):
                setattr(params, key, value)

    reply = ""
    error = ""
    async for event in query(params):
        if isinstance(event, CompletionEvent):
            reply = event.text
        elif isinstance(event, ErrorEvent):
            error = event.error
    if error:
        raise RuntimeError(error)
    return reply


async def run_subagent_streaming(
    *,
    provider: BaseProvider,
    system_prompt: str | SystemPrompt,
    user_text: str = "",
    tools: list[Tool] | None = None,
    parent_messages: list[Message] | None = None,
    initial_messages: list[Message] | None = None,
    max_turns: int = 10,
    agent_id: str = "",
    query_source: str = "",
    runtime_overrides: dict[str, Any] | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Run a sub-agent and yield its stream events."""
    messages: list[Message] = []
    if parent_messages:
        messages.extend(parent_messages)
    if initial_messages:
        messages.extend(initial_messages)
    elif user_text:
        messages.append(user_message(user_text))

    if isinstance(system_prompt, str):
        sp = SystemPrompt()
        sp.add_static(system_prompt)
    else:
        sp = system_prompt

    conv_id = uuid4().hex[:16]
    subagent_id = agent_id or f"subagent_{uuid4().hex[:8]}"
    effective_overrides = _subagent_runtime_overrides(
        runtime_overrides=runtime_overrides,
        provider=provider,
        messages=messages,
        tools=list(tools or []),
        system_prompt=sp,
        conversation_id=conv_id,
        agent_id=subagent_id,
    )

    params = QueryParams(
        provider=provider,
        system_prompt=sp,
        messages=messages,
        tools=list(tools or []),
        conversation_id=conv_id,
        agent_id=subagent_id,
        max_turns=max_turns,
        query_source=query_source,
        turn_state=SimpleNamespace(
            abort_event=effective_overrides.get("abort_event"),
        ) if effective_overrides.get("abort_event") is not None else None,
    )
    if effective_overrides:
        for key, value in effective_overrides.items():
            if hasattr(params, key):
                setattr(params, key, value)

    async for event in query(params):
        yield event


# ---------------------------------------------------------------------------
# Forked agent context — shared parent state for cache-efficient forks
# ---------------------------------------------------------------------------

@dataclass
class ForkedAgentContext:
    """Shared context from parent for spawning a forked agent.

    Mirrors Claude Code's ``CacheSafeParams`` — the fork inherits the
    parent's message prefix and system prompt so the LLM provider can
    reuse prompt cache entries.
    """

    parent_messages: list[Message]
    parent_system_prompt: str | SystemPrompt
    prompt_messages: list[Message] = field(default_factory=list)
    can_use_tool: Callable[[str], bool] = field(default=lambda _name: True)
    shared_state: dict[str, Any] = field(default_factory=dict)
    abort_signal: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class ForkedAgentResult:
    """Result returned by :func:`run_forked_agent`.

    ``usage`` mirrors forkedAgent.ts ``totalUsage`` (accumulated over the
    query loop), including cache token fields when emitted.
    """

    text: str
    tool_results: list[dict[str, Any]]
    messages_added: int
    aborted: bool
    usage: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
    )
    duration_ms: float = 0.0


def _accumulate_fork_usage(total: dict[str, int], usage_obj: Any) -> None:
    """Add tokens from a UsageEvent, UsageRecord, or similar object."""
    if usage_obj is None:
        return
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
        val = getattr(usage_obj, key, None)
        if val is None and isinstance(usage_obj, dict):
            val = usage_obj.get(key)
        if val is not None:
            total[key] = total.get(key, 0) + int(val)


async def run_forked_agent(
    *,
    context: ForkedAgentContext,
    fork_prompt: str = "",
    prompt_messages: list[Message] | None = None,
    provider: BaseProvider,
    tools: list[Tool] | None = None,
    max_turns: int = 10,
    max_output_tokens: int | None = None,
    agent_id: str = "",
    query_source: str = "",
    fork_label: str = "",
    on_message: Callable[[Any], None] | None = None,
    skip_transcript: bool = False,
) -> ForkedAgentResult:
    """Run a forked agent that shares the parent's message prefix.

    Deep-copies ``context.parent_messages`` so mutations stay isolated
    while the identical prefix enables prompt-cache sharing. Tools are
    filtered through ``context.can_use_tool`` before execution.

    ``fork_label`` matches forkedAgent.ts ``forkLabel`` (analytics tag);
    ``skip_transcript`` matches ``skipTranscript`` — when True, callers
    skip persisting a sidechain transcript; mini_agent has no
    ``recordSidechainTranscript`` hook yet, so this only logs at debug.
    """
    if fork_label or query_source:
        logger.debug(
            "run_forked_agent fork_label=%s query_source=%s",
            fork_label or "(none)",
            query_source or "(none)",
        )
    if skip_transcript:
        logger.debug("run_forked_agent skip_transcript=True (no session sidechain persistence in mini_agent)")

    _fork_start_time = time.monotonic()
    messages = copy.deepcopy(context.parent_messages)
    effective_prompt_messages = copy.deepcopy(prompt_messages or context.prompt_messages)
    if fork_prompt.strip():
        effective_prompt_messages.append(user_message(fork_prompt))
    messages.extend(effective_prompt_messages)

    filtered_tools = [
        t for t in (tools or []) if context.can_use_tool(t.name)
    ]

    if isinstance(context.parent_system_prompt, SystemPrompt):
        sp = context.parent_system_prompt
    else:
        sp = SystemPrompt()
        sp.add_static(context.parent_system_prompt)

    params = QueryParams(
        provider=provider,
        system_prompt=sp,
        messages=messages,
        tools=filtered_tools,
        conversation_id=f"fork-{uuid4().hex[:8]}",
        agent_id=agent_id or f"forked_{uuid4().hex[:8]}",
        max_turns=max_turns,
        max_output_tokens_override=max_output_tokens,
        query_source=query_source,
        turn_state=SimpleNamespace(abort_event=context.abort_signal),
    )

    reply_text = ""
    tool_results: list[dict[str, Any]] = []
    total_usage: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    messages_before = len(messages)
    aborted = False

    async for event in query(params):
        if context.abort_signal.is_set():
            aborted = True
            break
        if isinstance(event, CompletionEvent):
            reply_text = event.text
            # One CompletionEvent per assistant turn; usage matches the stream's
            # final tallies (do not also sum UsageEvent — would double-count).
            _accumulate_fork_usage(total_usage, event.usage)
        elif isinstance(event, ToolResultEvent):
            tool_results.append({
                "tool_use_id": event.tool_use_id,
                "tool_name": event.tool_name,
                "result": event.result,
                "is_error": event.is_error,
            })
        if on_message is not None:
            on_message(event)

    messages_added = len(messages) - messages_before
    duration_ms = (time.monotonic() - _fork_start_time) * 1000
    return ForkedAgentResult(
        text=reply_text,
        tool_results=tool_results,
        messages_added=messages_added,
        aborted=aborted,
        usage=total_usage,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Tool permission factories
# ---------------------------------------------------------------------------

_READ_ONLY_TOOLS = frozenset({
    "Read", "Grep", "Glob", "Bash", "list_files",
    "read_file", "search", "find_files",
})

_WRITE_TOOLS = frozenset({
    "Write", "Edit",
    "create_file", "patch_file",
})


def create_tool_filter(
    *,
    allowed: frozenset[str] | set[str] | None = None,
    blocked: frozenset[str] | set[str] | None = None,
    path_filter: Callable[[str, str], bool] | None = None,
) -> Callable[[str], bool]:
    """Build a tool permission callback.

    Parameters
    ----------
    allowed:
        If provided, only these tool names are permitted.
    blocked:
        If provided, these tool names are denied (applied after *allowed*).
    path_filter:
        Optional ``(tool_name, path) -> bool`` for path-based gating.
        Not evaluated here (tool names only) — meant for downstream use.
    """
    def _filter(tool_name: str) -> bool:
        if allowed is not None and tool_name not in allowed:
            return False
        if blocked is not None and tool_name in blocked:
            return False
        return True
    return _filter


def create_read_only_filter() -> Callable[[str], bool]:
    """Allow only read-only tools (file_read, grep, glob, bash, etc.)."""
    return create_tool_filter(allowed=_READ_ONLY_TOOLS)


def create_edit_only_filter(allowed_paths: list[str] | None = None) -> Callable[[str], bool]:
    """Allow read tools + write tools restricted to *allowed_paths*.

    The path restriction is advisory — the returned filter only gates
    by tool name. Path enforcement should happen at tool execution time.
    """
    combined = _READ_ONLY_TOOLS | _WRITE_TOOLS
    return create_tool_filter(allowed=combined)


def create_memory_agent_filter(memory_dir: str) -> Callable[[str], bool]:
    """Allow read tools + edit only within *memory_dir*.

    Same caveat as :func:`create_edit_only_filter` — the directory
    constraint is metadata; actual enforcement belongs in the tool layer.
    """
    combined = _READ_ONLY_TOOLS | _WRITE_TOOLS
    return create_tool_filter(allowed=combined)


def create_compact_agent_filter() -> Callable[[str], bool]:
    """Deny all tools — compact agents only summarise conversation."""
    return create_tool_filter(allowed=frozenset())


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------

class AgentLifecycle:
    """Manage start / stop / abort and timing for a running agent.

    Mirrors the async agent lifecycle in Claude Code's
    ``runAsyncAgentLifecycle``.
    """

    def __init__(
        self,
        *,
        max_runtime: float = 300.0,
        on_complete: Callable[[], Any] | None = None,
        on_error: Callable[[Exception], Any] | None = None,
    ) -> None:
        self._max_runtime = max_runtime
        self._on_complete = on_complete
        self._on_error = on_error
        self._start_time: float | None = None
        self._running = False
        self._abort_event = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None

    # -- properties --

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def elapsed_time(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.monotonic() - self._start_time

    @property
    def abort_event(self) -> asyncio.Event:
        return self._abort_event

    # -- control --

    def start(self, coro: Any) -> asyncio.Task[Any]:
        """Wrap *coro* in a managed :class:`asyncio.Task` with timeout."""
        if self._running:
            raise RuntimeError("Agent is already running")
        self._running = True
        self._start_time = time.monotonic()
        self._task = asyncio.ensure_future(self._run(coro))
        return self._task

    def stop(self) -> None:
        """Request a graceful stop (sets the abort event)."""
        self._abort_event.set()

    def abort(self) -> None:
        """Force-cancel the underlying task immediately."""
        self._abort_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    # -- internal --

    async def _run(self, coro: Any) -> Any:
        try:
            result = await asyncio.wait_for(coro, timeout=self._max_runtime)
            if self._on_complete:
                _maybe = self._on_complete()
                if asyncio.iscoroutine(_maybe):
                    await _maybe
            return result
        except asyncio.TimeoutError:
            logger.warning("Agent exceeded max_runtime=%.1fs", self._max_runtime)
            self._abort_event.set()
            if self._on_error:
                _maybe = self._on_error(TimeoutError(
                    f"Agent exceeded max runtime of {self._max_runtime}s"
                ))
                if asyncio.iscoroutine(_maybe):
                    await _maybe
        except asyncio.CancelledError:
            logger.info("Agent task cancelled")
            raise
        except Exception as exc:
            logger.exception("Agent task failed")
            if self._on_error:
                _maybe = self._on_error(exc)
                if asyncio.iscoroutine(_maybe):
                    await _maybe
        finally:
            self._running = False


# ---------------------------------------------------------------------------
# Agent summary tracker
# ---------------------------------------------------------------------------

# Same *shape* as upstream ``buildSummaryPrompt`` (instruction → Previous → examples).
# Placeholder file names only — not tied to any real project layout.
_SUMMARY_HEAD = (
    "Describe your most recent action in 3-5 words using present tense "
    "(-ing). Name the file or function, not the branch. Do not use tools.\n"
)
_SUMMARY_TAIL = """Good: "Reading config.py"
Good: "Fixing null check in handler"
Good: "Running unit tests"
Good: "Adding retry to API call"

Bad (past tense): "Analyzed the branch diff"
Bad (too vague): "Investigating the issue"
Bad (too long): "Reviewing full diff and module integration"
Bad (branch name): "Analyzed feature/background-summary branch only"
"""


class AgentSummaryTracker:
    """Periodically generate a 3-5 word progress summary for a running agent.

    Same role as upstream ``startAgentSummarization`` (~30s, 3--5 word line).
    Uses :func:`fork.run_forked_side_query` (no tool loop). Prompt tail uses
    generic placeholders, not real repo paths.
    """

    def __init__(
        self,
        *,
        provider: BaseProvider,
        interval: float = 30.0,
    ) -> None:
        self._provider = provider
        self._interval = interval
        self._summary = ""
        self._previous_summary: str | None = None
        self._stopped = False
        self._task: asyncio.Task[Any] | None = None

    @property
    def summary(self) -> str:
        return self._summary

    def get_summary(self) -> str:
        return self._summary

    def start(self, messages_fn: Callable[[], list[Message]]) -> None:
        """Begin periodic summarisation.

        *messages_fn* is called each tick to get the latest messages.
        """
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.ensure_future(self._loop(messages_fn))

    def stop(self) -> None:
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def update_summary(self, messages: list[Message]) -> str:
        """Re-generate the summary from *messages* (one-shot)."""
        if len(messages) < 3:
            return self._summary

        prev_line = ""
        if self._previous_summary:
            prev_line = f'\nPrevious: "{self._previous_summary}" — say something NEW.\n'

        prompt = f"{_SUMMARY_HEAD}{prev_line}\n{_SUMMARY_TAIL}"

        from .fork import run_forked_side_query

        text = await run_forked_side_query(
            provider=self._provider,
            parent_messages=messages[-20:],
            system_prompt="",
            prompt=prompt,
            max_tokens=60,
            temperature=0.3,
            query_source="agent_summary",
        )
        text = text.strip().strip('"').strip("'")
        if text:
            self._previous_summary = text
            self._summary = text
        return self._summary

    async def _loop(self, messages_fn: Callable[[], list[Message]]) -> None:
        while not self._stopped:
            await asyncio.sleep(self._interval)
            if self._stopped:
                break
            try:
                msgs = messages_fn()
                await self.update_summary(msgs)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("Summary update failed", exc_info=True)
