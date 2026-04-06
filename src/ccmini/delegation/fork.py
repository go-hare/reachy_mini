"""Session forking — branch conversations sharing prompt cache prefix.

Mirrors Claude Code's fork-session + side-query patterns:
- ``fork()``: create a branch from current messages, run independently
- ``side_query()``: lightweight one-shot query that doesn't pollute main history

Extended features (ported from Claude Code's forkedAgent / AgentSummary):
- Enhanced ``fork_query()`` with tool filtering, abort, and progress callbacks
- ``run_forked_side_query()``: one-shot fork for services like SessionMemory
- ``create_memory_file_tool_filter()``: path-scoped write permission
- Enhanced ``side_query()`` with source tagging, abort, and token streaming
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from ..messages import CompletionEvent, Message, StreamEvent, TextBlock, TextEvent, user_message
from ..prompts import SystemPrompt
from ..providers import BaseProvider
from ..tool import Tool

logger = logging.getLogger(__name__)


async def fork_query(
    *,
    provider: BaseProvider,
    system_prompt: SystemPrompt | str,
    base_messages: list[Message],
    fork_prompt: str,
    max_turns: int = 5,
    query_source: str = "",
    tools: list[Tool] | None = None,
    can_use_tool: Callable[[str], bool] | None = None,
    abort_signal: asyncio.Event | None = None,
    on_progress: Callable[[StreamEvent], Any] | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Run a forked conversation from a snapshot of the main history.

    The base_messages are deep-copied so the fork cannot mutate the
    original conversation.  This shares the prompt-cache prefix with
    the main session (same system prompt + shared message prefix).

    Parameters
    ----------
    tools:
        Tool definitions available to this fork (same semantics as
        :class:`~mini_agent.engine.query.QueryParams.tools`).
    can_use_tool:
        Optional callback ``(tool_name) -> bool``.  When provided *and*
        ``tools`` is non-empty, only tools passing this filter are kept.
    abort_signal:
        Optional :class:`asyncio.Event`.  When set, the fork stops
        yielding events and returns.
    on_progress:
        Optional callback invoked for every event before it is yielded.
    """
    from ..engine.query import QueryParams, query

    messages = copy.deepcopy(base_messages)
    messages.append(user_message(fork_prompt))

    tool_list = list(tools) if tools else []
    params = QueryParams(
        provider=provider,
        system_prompt=system_prompt,
        messages=messages,
        tools=tool_list,
        conversation_id=f"fork-{uuid4().hex[:8]}",
        agent_id="fork",
        max_turns=max_turns,
        query_source=query_source,
        turn_state=SimpleNamespace(abort_event=abort_signal),
    )

    if can_use_tool is not None and params.tools:
        params = replace(
            params,
            tools=[t for t in params.tools if can_use_tool(t.name)],
        )

    async for event in query(params):
        if abort_signal is not None and abort_signal.is_set():
            return
        if on_progress is not None:
            on_progress(event)
        yield event


async def side_query(
    *,
    provider: BaseProvider,
    system_prompt: str = "",
    context_messages: list[Message] | None = None,
    prompt: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    query_source: str = "",
    abort_signal: asyncio.Event | None = None,
    on_token: Callable[[str], Any] | None = None,
    stop_sequences: list[str] | None = None,
) -> str:
    """Lightweight one-shot query for summaries, analysis, or classification.

    By default uses the provider's non-streaming ``complete()`` for
    efficiency.  If *on_token* is set, uses ``stream()`` so each text
    chunk can be forwarded (see *on_token* below).

    Aligns with ``sideQuery`` in the reference (``utils/sideQuery.ts``) for
    the OpenAI-compatible subset: ``max_tokens``, ``temperature``,
    ``stop_sequences``, ``query_source``.  Anthropic-only fields (thinking,
    output_format betas, OAuth fingerprint) are not modeled here.

    Parameters
    ----------
    query_source:
        Tag for logging origin (e.g. ``"session_memory"``, ``"auto_dream"``).
    abort_signal:
        Optional :class:`asyncio.Event`.  If set before/during the call,
        returns an empty string immediately.
    on_token:
        Optional callback invoked for each streamed text chunk when set
        (implemented via :meth:`~mini_agent.providers.BaseProvider.stream`).
        When omitted, the response is fetched with ``complete()`` in one shot.
    stop_sequences:
        Passed to the provider as ``stop`` / ``stop_sequences`` where supported
        (see :meth:`BaseProvider.complete`).
    """
    if abort_signal is not None and abort_signal.is_set():
        return ""

    if query_source:
        logger.debug("side_query source=%s prompt_len=%d", query_source, len(prompt))

    messages: list[Message] = []
    if context_messages:
        messages.extend(copy.deepcopy(context_messages))
    messages.append(user_message(prompt))

    if on_token is not None:
        text_parts: list[str] = []
        async for event in provider.stream(
            messages=messages,
            system=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            query_source=query_source,
            stop_sequences=stop_sequences,
        ):
            if abort_signal is not None and abort_signal.is_set():
                return ""
            if isinstance(event, TextEvent) and event.text:
                on_token(event.text)
                text_parts.append(event.text)
        # Stream deltas must concatenate like engine/query (+=), not join with newlines.
        return "".join(text_parts)

    result = await provider.complete(
        messages=messages,
        system=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        query_source=query_source,
        stop_sequences=stop_sequences,
    )

    if abort_signal is not None and abort_signal.is_set():
        return ""

    if isinstance(result.content, str):
        text = result.content
    else:
        parts = [b.text for b in result.content if isinstance(b, TextBlock)]
        text = "\n".join(parts)

    return text


async def summarize_conversation(
    *,
    provider: BaseProvider,
    messages: list[Message],
    max_tokens: int = 1024,
) -> str:
    """Summarize a conversation history using a side query."""
    return await side_query(
        provider=provider,
        system_prompt="Summarize the following conversation concisely.",
        context_messages=messages,
        prompt="Please provide a brief summary of our conversation so far.",
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# Forked side query — one-shot fork for background services
# ---------------------------------------------------------------------------

async def run_forked_side_query(
    *,
    provider: BaseProvider,
    parent_messages: list[Message],
    system_prompt: str = "",
    prompt: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    query_source: str = "",
    abort_signal: asyncio.Event | None = None,
    stop_sequences: list[str] | None = None,
) -> str:
    """One-shot forked query that shares the parent's message context.

    Combines the fork (deep-copied parent messages) with
    :func:`side_query` semantics (no tools, single LLM completion).
    This is the recommended entry-point for background services like
    ``SessionMemory``, ``ExtractMemories``, and ``AutoDream``.

    Unlike :func:`fork_query`, this does **not** enter a tool loop —
    it sends a single request and returns the text response.
    """
    if abort_signal is not None and abort_signal.is_set():
        return ""

    if query_source:
        logger.debug(
            "run_forked_side_query source=%s parent_msgs=%d",
            query_source, len(parent_messages),
        )

    forked_messages = copy.deepcopy(parent_messages)

    return await side_query(
        provider=provider,
        system_prompt=system_prompt,
        context_messages=forked_messages,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        query_source=query_source,
        abort_signal=abort_signal,
        stop_sequences=stop_sequences,
    )


# ---------------------------------------------------------------------------
# Memory-file tool filter
# ---------------------------------------------------------------------------

_MEMORY_READ_TOOLS = frozenset({
    "Read", "Grep", "Glob", "Bash",
    "list_files", "search", "find_files",
})

_MEMORY_WRITE_TOOLS = frozenset({
    "Edit", "Write",
    "create_file", "patch_file",
})


def create_memory_file_tool_filter(
    memory_dir: str,
) -> Callable[[str], bool]:
    """Return a tool permission callback for memory-agent forks.

    Allows:
      - ``file_read``, ``grep``, ``glob``, ``bash`` (read-only) — unrestricted
      - ``Edit``, ``Write`` — ONLY for paths starting with *memory_dir*
      - Everything else — blocked

    The *memory_dir* constraint is enforced at the **name level** here
    (write tools are allowed) and must also be enforced at **execution
    time** by checking paths against *memory_dir*.  The filter is
    deliberately kept name-only so it composes with the tool-gating in
    :func:`fork_query` and :func:`run_forked_agent`.
    """
    all_allowed = _MEMORY_READ_TOOLS | _MEMORY_WRITE_TOOLS

    def _filter(tool_name: str) -> bool:
        return tool_name in all_allowed

    _filter.memory_dir = memory_dir  # type: ignore[attr-defined]
    return _filter
