"""Core query loop — the heart of the mini-agent engine.

Ported from Claude Code's ``query.ts``: an async generator that
streams events, handles tool calls, and manages the conversation
state within a single turn.

Per-iteration preprocessing order matches ``query.ts`` (lines ~365–447):
compact boundary → ``apply_tool_result_budget`` → snip → microcompact →
context collapse → autocompact → API call.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..attachments import AttachmentCollector
from ..session.store import get_session_transcript_path
from .compact import (
    build_post_compact_messages,
    CompactConfig,
    CompactTracker,
    auto_compact_if_needed,
    calculate_token_warning_state,
    estimate_tokens,
    micro_compact,
    PROMPT_TOO_LONG_ERROR_MESSAGE,
    reactive_compact,
)
from .history_snip import SnipConfig, snip_if_needed
from .context_collapse import (
    CollapseConfig,
    apply_collapses,
    is_context_collapse_enabled,
    recover_from_overflow,
)
from ..providers.fallback import FallbackConfig, ModelFallbackManager, is_overloaded_error
from ..hooks.runner import HookRunner
from ..hooks import OnStreamEventHook, PostQueryHook, PostSamplingContext, PreQueryHook, ToolUseDecision
from ..providers.retry import ContextTooLongError
from ..messages import (
    CompletionEvent,
    ErrorEvent,
    get_messages_after_compact_boundary,
    Message,
    normalize_messages_for_api,
    PendingToolCallEvent,
    RequestStartEvent,
    StreamEvent,
    ThinkingEvent,
    TextBlock,
    TextEvent,
    ToolCallEvent,
    ToolResultBlock,
    ToolResultEvent,
    ToolUseBlock,
    ToolUseSummaryEvent,
    UsageEvent,
    assistant_message,
    tool_result_message,
    user_message,
)
from ..usage import UsageRecord
from ..services.relevant_memory_prefetch import await_relevant_memory_prefetch_if_enabled
from ..prompts import SystemPrompt
from ..providers import BaseProvider
from ..permissions import (
    PermissionChecker,
    PermissionDecision,
    build_permission_denied_message,
)
from .stop import (
    EnhancedStopChecker,
    OutputTokensEscalation,
    StopCircuitBreaker,
    StopDecision,
)
from ..tool import ClientTool, Tool, ToolUseContext
from .result_budget import (
    apply_tool_result_budget,
    is_aggregate_budget_feature_enabled,
    resolve_tool_results_dir_for_agent,
    skip_tool_names_for_aggregate_budget,
)
from .streaming_executor import StreamingToolExecutor

if TYPE_CHECKING:
    from ..agent import Agent


@dataclass
class QueryParams:
    """All inputs to a single query() call."""
    provider: BaseProvider
    system_prompt: SystemPrompt | str
    messages: list[Message]
    tools: list[Tool] = field(default_factory=list)
    agent: "Agent | None" = None
    conversation_id: str = ""
    agent_id: str = ""
    max_turns: int = 20
    compact_threshold: int = 100_000
    compact_config: CompactConfig | None = None
    compact_tracker: CompactTracker | None = None
    pre_hooks: list[PreQueryHook] = field(default_factory=list)
    post_hooks: list[PostQueryHook] = field(default_factory=list)
    stream_hooks: list[OnStreamEventHook] = field(default_factory=list)
    permission_checker: PermissionChecker | None = None
    attachment_collector: AttachmentCollector | None = None
    hook_runner: HookRunner | None = None
    fallback_config: FallbackConfig | None = None
    summary_provider: BaseProvider | None = None
    on_tool_summary: Any = None
    session_memory_content: str | None = None
    snip_config: SnipConfig | None = None
    collapse_config: CollapseConfig | None = None
    max_output_tokens_override: int | None = None
    turn_state: Any = None
    working_directory: str = ""
    query_source: str = ""
    is_non_interactive: bool = False
    # TS: userContext / systemContext — injected as <system-reminder> tags
    user_context: dict[str, str] = field(default_factory=dict)
    system_context: dict[str, str] = field(default_factory=dict)
    # TS: taskBudget — API task_budget (Anthropic beta)
    task_budget: dict[str, int] | None = None


@dataclass
class _LoopState:
    """Mutable state for a single query loop execution.

    Mirrors TS ``State`` in query.ts — every field here has a 1:1
    counterpart in the TypeScript version.
    """
    messages: list[Message]
    # TS: turnCount — starts at 1, incremented at the continue site (next_turn).
    turn_count: int = 1
    max_output_tokens_recovery_count: int = 0
    pending_client_calls: list[ToolCallEvent] = field(default_factory=list)
    max_output_tokens_override: int | None = None
    transitions: list[str] = field(default_factory=list)
    has_attempted_reactive_compact: bool = False
    # TS: stopHookActive — set to True when stop-hook blocking errors
    # are injected so the next iteration knows it's a stop-hook retry.
    stop_hook_active: bool | None = None
    # TS: transition — why the previous iteration continued.
    # Undefined on first iteration. Lets tests assert recovery paths.
    last_transition: str | None = None
    # TS: pendingToolUseSummary — deferred summary from previous iteration (async task)
    pending_tool_use_summary: Any = None
    # TS: autoCompactTracking — carried across iterations, reset on compact.
    auto_compact_tracking: CompactTracker | None = None

    @property
    def last_user_text(self) -> str:
        for msg in reversed(self.messages):
            if msg.role == "user":
                return msg.text
        return ""


MAX_RECOVERY = 3
CAPPED_DEFAULT_MAX_TOKENS = 8_000
ESCALATED_MAX_TOKENS = 64_000


async def query(params: QueryParams) -> AsyncGenerator[StreamEvent, None]:
    """The core query loop.

    Yields StreamEvents as they occur: text chunks, tool calls/results,
    and the final CompletionEvent.  The caller (Agent) drives this
    generator via ``async for event in query(params)``.
    """
    tool_map: dict[str, Tool] = {}
    for tool in params.tools:
        tool_map[tool.name] = tool
        for alias in getattr(tool, "aliases", ()):
            tool_map.setdefault(alias, tool)
    state = _LoopState(
        messages=list(params.messages),
        max_output_tokens_override=params.max_output_tokens_override,
    )
    stop_checker = EnhancedStopChecker()
    stop_breaker = StopCircuitBreaker()
    output_tokens_escalation = OutputTokensEscalation()
    hook_runner = params.hook_runner
    active_provider = params.provider

    fallback_mgr: ModelFallbackManager | None = None
    if params.fallback_config and params.fallback_config.enabled and params.fallback_config.fallback_model:
        fallback_mgr = ModelFallbackManager(params.fallback_config, params.provider)

    if isinstance(params.system_prompt, SystemPrompt):
        from ..providers.anthropic import AnthropicProvider
        if isinstance(params.provider, AnthropicProvider):
            system_text: str | list[dict[str, Any]] = params.system_prompt.render_with_cache_markers()
        else:
            system_text = params.system_prompt.render()
    else:
        system_text = params.system_prompt

    # TS appendSystemContext: append systemContext to system prompt
    if params.system_context:
        context_suffix = "\n".join(
            f"{key}: {value}" for key, value in params.system_context.items()
        )
        if isinstance(system_text, str):
            system_text = system_text + "\n" + context_suffix if system_text else context_suffix
        elif isinstance(system_text, list):
            # For Anthropic cache-marker format, append as a new text block
            system_text.append({"type": "text", "text": context_suffix})

    state.max_output_tokens_override = params.max_output_tokens_override
    in_progress_tool_use_ids: set[str] = set()
    has_interruptible_tool_in_progress = False

    def _set_in_progress_tool_use_ids(next_ids: set[str]) -> None:
        nonlocal in_progress_tool_use_ids
        in_progress_tool_use_ids = set(next_ids)

    def _set_has_interruptible_tool_in_progress(value: bool) -> None:
        nonlocal has_interruptible_tool_in_progress
        has_interruptible_tool_in_progress = bool(value)

    context = ToolUseContext(
        conversation_id=params.conversation_id,
        agent_id=params.agent_id,
        turn_id=uuid4().hex[:16],
        messages=list(state.messages),
        system_prompt=system_text,
        read_file_state=_get_read_file_state(),
        query_tracking={"transitions": state.transitions, "chain_id": uuid4().hex},
        turn_state=params.turn_state,
        content_replacement_state=(
            getattr(params.agent, "_content_replacement_state", None)
            if params.agent is not None
            else None
        ),
        options={"tools": list(params.tools)},
        abort_event=getattr(params.turn_state, "abort_event", None),
        set_in_progress_tool_use_ids=_set_in_progress_tool_use_ids,
        set_has_interruptible_tool_in_progress=_set_has_interruptible_tool_in_progress,
        extras={
            "agent": params.agent,
            "permission_checker": params.permission_checker,
            "messages": list(state.messages),
            "system_prompt": system_text,
            "query_transitions": state.transitions,
            "hook_runner": params.hook_runner,
            "attachment_collector": params.attachment_collector,
            "summary_provider": params.summary_provider,
            "session_memory_content": params.session_memory_content,
            "compact_threshold": params.compact_threshold,
            "fallback_config": params.fallback_config,
            "working_directory": params.working_directory,
            "query_source": params.query_source,
            "is_non_interactive": params.is_non_interactive,
            "in_progress_tool_use_ids": in_progress_tool_use_ids,
            "has_interruptible_tool_in_progress": has_interruptible_tool_in_progress,
            "team_name": str(getattr(getattr(params.agent, "_team_create_tool", None), "_active_team_name", "")).strip(),
            "agent_name": (
                str(params.agent_id).split("@", 1)[0]
                if "@" in str(params.agent_id)
                else str(params.agent_id or "").strip()
            ),
        },
    )

    if params.attachment_collector is not None:
        _agent = params.agent
        att_context = {
            "conversation_id": params.conversation_id,
            "user_text": state.last_user_text,
            "agent_id": params.agent_id,
            "messages": list(state.messages),
            "buddy_enabled": getattr(_agent, "_buddy_enabled", True) if _agent is not None else True,
            "companion_muted": getattr(_agent, "_companion_muted", None) if _agent is not None else None,
        }
        attachments = await params.attachment_collector.collect(att_context)
        state.messages = params.attachment_collector.inject(attachments, state.messages)

    compact_cfg = params.compact_config or CompactConfig(
        context_window=params.compact_threshold + 33_000,
    )
    compact_tracker = params.compact_tracker or CompactTracker()

    snip_cfg = params.snip_config or SnipConfig(
        max_context_tokens=compact_cfg.context_window,
    )
    collapse_cfg = params.collapse_config or CollapseConfig()
    session_store = getattr(params.agent, "_session_store", None) if params.agent is not None else None
    session_dir = getattr(session_store, "session_dir", None)
    transcript_path = str(get_session_transcript_path(params.conversation_id, session_dir))

    while True:
        # TS: destructure state at top of each iteration.
        # turnCount is NOT incremented here — it starts at 1 and is
        # incremented at the next_turn continue site (TS line 1719).
        # compact_tracker.turn_counter is only incremented after tool
        # execution when tracking.compacted (TS line 1523-1533).

        # TS line 337: yield stream_request_start at top of each iteration
        yield RequestStartEvent()
        thinking_status_active = True
        mapped_thinking_start = await _apply_stream_hooks(
            ThinkingEvent(phase="start", source="status"),
            params.stream_hooks,
            agent=params.agent,
        )
        if mapped_thinking_start is not None:
            yield mapped_thinking_start

        context.messages = list(state.messages)
        context.extras["messages"] = list(state.messages)
        context.extras["in_progress_tool_use_ids"] = set(in_progress_tool_use_ids)
        context.extras["has_interruptible_tool_in_progress"] = has_interruptible_tool_in_progress
        _prev_depth = context.query_tracking.get("depth")
        _chain_depth = 0 if _prev_depth is None else int(_prev_depth) + 1
        context.query_tracking = {
            "transitions": list(state.transitions),
            "turn_count": state.turn_count,
            "chain_id": context.query_tracking.get("chain_id", uuid4().hex),
            "depth": _chain_depth,
        }
        if params.agent is not None:
            ensure = getattr(params.agent, "_ensure_content_replacement_state", None)
            if callable(ensure):
                ensure()
            context.content_replacement_state = getattr(
                params.agent, "_content_replacement_state", None
            )

        # Phase 0: TS line 365 — get messages after compact boundary FIRST
        messages_for_query = list(get_messages_after_compact_boundary(state.messages))

        # Phase 0.25: TS line 379-394 — aggregate tool-result budget before snip.
        def _content_replacement_transcript_writer(recs: list[Any]) -> None:
            ag = params.agent
            if ag is None:
                return
            fn = getattr(ag, "_append_content_replacement_records", None)
            if callable(fn):
                fn(recs)

        _write_tr = (
            _content_replacement_transcript_writer
            if (
                params.agent is not None
                and getattr(params.agent, "_session_store", None) is not None
                and is_aggregate_budget_feature_enabled()
            )
            else None
        )
        _tool_results_dir = (
            resolve_tool_results_dir_for_agent(params.agent) if params.agent is not None else None
        )
        messages_for_query = await apply_tool_result_budget(
            messages_for_query,
            context.content_replacement_state,
            write_to_transcript=_write_tr,
            skip_tool_names=skip_tool_names_for_aggregate_budget(params.tools),
            tool_results_dir=_tool_results_dir,
        )

        # Phase 0.5: history snip — feature-gated in the reference tree.
        # TS line 400-410: snip on messagesForQuery (post-boundary).
        messages_for_query, _snip_freed = snip_if_needed(messages_for_query, snip_cfg)

        # Phase 1: micro-compact old tool results
        # TS line 414-426: microcompact on messagesForQuery.
        messages_for_query, _ = micro_compact(
            messages_for_query,
            config=compact_cfg,
            query_source=params.query_source,
        )

        # Phase 2: context collapse — read-time projection before autocompact.
        # TS line 440-447: collapse on messagesForQuery.
        if is_context_collapse_enabled(collapse_cfg):
            projected, collapse_saved = apply_collapses(messages_for_query, collapse_cfg)
            api_messages = projected if collapse_saved > 0 else messages_for_query
        else:
            collapse_saved = 0
            api_messages = messages_for_query
        context.messages = list(state.messages)
        context.extras["messages"] = list(state.messages)

        # Phase 3: auto-compact if still over threshold
        # TS line 454-543: autocompact on messagesForQuery.
        compaction_result, _compact_success = await auto_compact_if_needed(
            api_messages,
            params.provider,
            config=compact_cfg,
            tracker=compact_tracker,
            session_memory_content=params.session_memory_content,
            query_source=params.query_source,
            extra_tokens_freed=_snip_freed,
            transcript_path=transcript_path,
            conversation_id=params.conversation_id,
        )
        if compaction_result is not None:
            # TS line 521-526: reset tracking on every compact.
            # CompactTracker.record_success() already resets turn_counter
            # and consecutive_failures inside auto_compact_if_needed.
            state.messages = build_post_compact_messages(compaction_result)
            messages_for_query = list(get_messages_after_compact_boundary(state.messages))
            api_messages = messages_for_query
        context.messages = list(state.messages)
        context.extras["messages"] = list(state.messages)

        accumulated_text = ""
        tool_calls: list[ToolCallEvent] = []
        denied_tool_calls: list[ToolCallEvent] = []
        precomputed_results: list[ToolResultBlock] = []
        turn_usage: UsageRecord | None = None
        eager_executor: StreamingToolExecutor | None = None
        eager_started_ids: set[str] = set()
        last_stop_reason: str | None = None
        # TS: needsFollowUp — set True when any tool_use block arrives.
        # Determines whether we enter the tool-execution branch or the
        # end-of-turn / recovery branch after streaming.
        needs_follow_up = False
        # TS: isWithheldMaxOutputTokens — the last assistant message had
        # apiError === 'max_output_tokens'.  We withhold it from yield
        # until we know whether recovery can continue.
        withheld_max_output_tokens = False
        # TS: isWithheld413 — prompt-too-long error withheld for recovery.
        withheld_prompt_too_long = False

        # Blocking limit check — TS checks before API call (line 628-648).
        # Skip when compaction just happened (the result is already validated),
        # skip for compact/session_memory sources (they need to run to REDUCE
        # the token count), skip when reactive compact is enabled with
        # auto-compact (let the real API 413 trigger recovery), and skip when
        # context collapse is enabled with auto-compact.
        # TS: !(reactiveCompact?.isReactiveCompactEnabled() && isAutoCompactEnabled()) && !collapseOwnsIt
        _collapse_owns_it = is_context_collapse_enabled(collapse_cfg) and compact_cfg.enabled
        _reactive_compact_skips = compact_cfg.enabled  # reactive compact always available in Python
        if (
            compaction_result is None
            and params.query_source not in ("compact", "session_memory")
            and not _reactive_compact_skips
            and not _collapse_owns_it
        ):
            token_est = estimate_tokens(api_messages) - _snip_freed
            warning_state = calculate_token_warning_state(token_est, compact_cfg)
            if warning_state.is_at_blocking_limit:
                yield ErrorEvent(
                    error=PROMPT_TOO_LONG_ERROR_MESSAGE,
                    recoverable=False,
                )
                params.messages[:] = state.messages
                return

        try:
            provider = fallback_mgr.get_active_provider() if fallback_mgr else active_provider
            await await_relevant_memory_prefetch_if_enabled(params.conversation_id)
            # TS prependUserContext: inject userContext as <system-reminder> BEFORE normalize
            pre_api_messages = list(api_messages)
            if params.user_context:
                context_lines = "\n".join(
                    f"# {key}\n{value}" for key, value in params.user_context.items()
                )
                reminder_text = (
                    "<system-reminder>\n"
                    "As you answer the user's questions, you can use the following context:\n"
                    f"{context_lines}\n\n"
                    "IMPORTANT: this context may or may not be relevant to your tasks. "
                    "You should not respond to this context unless it is highly relevant "
                    "to your task.\n</system-reminder>\n"
                )
                pre_api_messages = [user_message(reminder_text, isMeta=True)] + pre_api_messages
            # TS normalizeMessagesForAPI: prepare messages before API call
            normalized_messages = normalize_messages_for_api(pre_api_messages, params.tools)
            async for event in provider.stream(
                messages=normalized_messages,
                system=system_text,
                tools=params.tools if params.tools else None,
                max_tokens=state.max_output_tokens_override,
                task_budget=params.task_budget,
                query_source=params.query_source,
            ):
                if (
                    thinking_status_active
                    and isinstance(event, (TextEvent, ToolCallEvent, ErrorEvent))
                ):
                    mapped_thinking_end = await _apply_stream_hooks(
                        ThinkingEvent(phase="end", source="status"),
                        params.stream_hooks,
                        agent=params.agent,
                    )
                    if mapped_thinking_end is not None:
                        yield mapped_thinking_end
                    thinking_status_active = False

                if isinstance(event, TextEvent):
                    accumulated_text += event.text
                    mapped = await _apply_stream_hooks(event, params.stream_hooks, agent=params.agent)
                    if mapped is not None:
                        yield mapped
                    if eager_executor is not None:
                        for ready in eager_executor.get_completed_results():
                            if ready.new_context is not None:
                                context = ready.new_context
                            if ready.message is None:
                                continue
                            mapped_ready = await _apply_stream_hooks(ready.message, params.stream_hooks, agent=params.agent)
                            if mapped_ready is not None:
                                yield mapped_ready

                elif isinstance(event, ToolCallEvent):
                    current_call = event
                    denied_this_call: ToolResultBlock | None = None

                    if hook_runner:
                        pre_result = await hook_runner.run_pre_tool_use(current_call, agent=params.agent)  # type: ignore[arg-type]
                        if pre_result.decision == ToolUseDecision.DENY:
                            denied_this_call = ToolResultBlock(
                                tool_use_id=current_call.tool_use_id,
                                content=(
                                    f"Blocked by hook: {pre_result.reason}\n"
                                    "IMPORTANT: Do not retry the exact same tool call. "
                                    "Use a different approach, or explain to the user "
                                    "why this blocked capability is needed."
                                ),
                                is_error=True,
                            )
                        elif pre_result.decision == ToolUseDecision.MODIFY and pre_result.modified_input:
                            current_call = ToolCallEvent(
                                tool_use_id=current_call.tool_use_id,
                                tool_name=current_call.tool_name,
                                tool_input=pre_result.modified_input,
                            )

                    if denied_this_call is None and params.permission_checker is not None:
                        tool = tool_map.get(current_call.tool_name)
                        is_ro = tool.is_read_only_call(current_call.tool_input) if tool else False
                        decision = await params.permission_checker.resolve(
                            current_call.tool_name,
                            current_call.tool_input,
                            is_read_only=is_ro,
                            tool_use_id=current_call.tool_use_id,
                        )
                        if decision == PermissionDecision.DENY:
                            denied_this_call = ToolResultBlock(
                                tool_use_id=current_call.tool_use_id,
                                content=build_permission_denied_message(
                                    current_call.tool_name,
                                    mode=getattr(params.permission_checker, "mode", None),
                                ),
                                is_error=True,
                            )

                    if denied_this_call is not None:
                        denied_tool_calls.append(current_call)
                        precomputed_results.append(denied_this_call)
                        mapped = await _apply_stream_hooks(event, params.stream_hooks, agent=params.agent)
                        if mapped is not None:
                            yield mapped
                        denied_event = ToolResultEvent(
                            tool_use_id=denied_this_call.tool_use_id,
                            tool_name=current_call.tool_name,
                            result=denied_this_call.content,
                            is_error=True,
                        )
                        mapped_denied = await _apply_stream_hooks(denied_event, params.stream_hooks, agent=params.agent)
                        if mapped_denied is not None:
                            yield mapped_denied
                    else:
                        tool_calls.append(current_call)
                        # TS: needsFollowUp = true when tool_use blocks arrive
                        needs_follow_up = True
                        tool = tool_map.get(current_call.tool_name)
                        if (
                            tool is not None
                            and tool.is_concurrency_safe(current_call.tool_input)
                            and not isinstance(tool, ClientTool)
                        ):
                            if eager_executor is None:
                                eager_executor = StreamingToolExecutor(tool_map, context)
                            eager_executor.add_tool(current_call)
                            eager_started_ids.add(current_call.tool_use_id)

                    if denied_this_call is None:
                        mapped = await _apply_stream_hooks(event, params.stream_hooks, agent=params.agent)
                        if mapped is not None:
                            yield mapped
                    if eager_executor is not None:
                        for ready in eager_executor.get_completed_results():
                            if ready.new_context is not None:
                                context = ready.new_context
                            if ready.message is None:
                                continue
                            mapped_ready = await _apply_stream_hooks(ready.message, params.stream_hooks, agent=params.agent)
                            if mapped_ready is not None:
                                yield mapped_ready

                elif isinstance(event, UsageEvent):
                    turn_usage = UsageRecord(
                        input_tokens=event.input_tokens,
                        output_tokens=event.output_tokens,
                        cache_read_tokens=event.cache_read_tokens,
                        cache_creation_tokens=event.cache_creation_tokens,
                        model=event.model,
                    )
                    if event.stop_reason:
                        last_stop_reason = event.stop_reason
                    yield event
                    if eager_executor is not None:
                        for ready in eager_executor.get_completed_results():
                            if ready.new_context is not None:
                                context = ready.new_context
                            if ready.message is None:
                                continue
                            mapped_ready = await _apply_stream_hooks(ready.message, params.stream_hooks, agent=params.agent)
                            if mapped_ready is not None:
                                yield mapped_ready

                elif isinstance(event, ThinkingEvent):
                    mapped = await _apply_stream_hooks(
                        event, params.stream_hooks, agent=params.agent
                    )
                    if mapped is not None:
                        yield mapped

                elif isinstance(event, ErrorEvent):
                    # TS line 984: yield missing tool results before error; conversation
                    # must also contain assistant + tool_result blocks (same as generic
                    # provider error path below).
                    if tool_calls:
                        assistant_msg = _append_assistant_turn(
                            state.messages, accumulated_text, tool_calls
                        )
                        missing_results = _build_missing_tool_results(
                            tool_calls,
                            event.error or "Stream error",
                        )
                        state.messages.append(
                            _build_tool_result_message(
                                missing_results,
                                assistant_uuid=str(assistant_msg.metadata.get("uuid", "")) if assistant_msg else "",
                            )
                        )
                        context.messages = list(state.messages)
                        context.extras["messages"] = list(state.messages)
                        for tc in tool_calls:
                            tre = ToolResultEvent(
                                tool_use_id=tc.tool_use_id,
                                tool_name=tc.tool_name,
                                result=event.error or "Stream error",
                                is_error=True,
                            )
                            mapped_tre = await _apply_stream_hooks(
                                tre, params.stream_hooks, agent=params.agent
                            )
                            if mapped_tre is not None:
                                yield mapped_tre
                    mapped_error = await _apply_stream_hooks(event, params.stream_hooks, agent=params.agent)
                    if mapped_error is not None:
                        yield mapped_error
                    params.messages[:] = state.messages
                    return

            if thinking_status_active:
                mapped_thinking_end = await _apply_stream_hooks(
                    ThinkingEvent(phase="end", source="status"),
                    params.stream_hooks,
                    agent=params.agent,
                )
                if mapped_thinking_end is not None:
                    yield mapped_thinking_end
                thinking_status_active = False

            if fallback_mgr:
                fallback_mgr.record_success()
            stop_breaker.record_success()
            # TS does NOT reset recovery counts here — they are only reset
            # at the next_turn continue site (TS line 1720-1721).
            # output_tokens_escalation is also NOT reset here.

            # TS line 999-1009: execute post-sampling hooks BEFORE abort check
            if accumulated_text.strip() or tool_calls:
                if hook_runner and hook_runner.post_sampling:
                    _fire_post_sampling(
                        hook_runner, state.messages, system_text,
                        accumulated_text, tool_calls, agent=params.agent,
                    )

            # TS abort check (query.ts 1015-1050): check if aborted during streaming
            _abort_event = getattr(context, "abort_event", None) or getattr(params.turn_state, "abort_event", None)
            _is_aborted = (
                _abort_event is not None
                and callable(getattr(_abort_event, "is_set", None))
                and _abort_event.is_set()
            )
            if _is_aborted:
                # Same order as normal tool path: assistant first, stream executor
                # events, then one user message with all tool_result blocks.
                if eager_executor and tool_calls:
                    assistant_msg = _append_assistant_turn(
                        state.messages, accumulated_text, tool_calls
                    )
                    async for update in eager_executor.get_remaining_results():
                        if update.new_context is not None:
                            context = update.new_context
                        if update.message is not None:
                            mapped_u = await _apply_stream_hooks(
                                update.message, params.stream_hooks, agent=params.agent
                            )
                            if mapped_u is not None:
                                yield mapped_u
                    blocks = list(eager_executor.get_result_blocks())
                    seen_ids = {b.tool_use_id for b in blocks}
                    for tc in tool_calls:
                        if tc.tool_use_id not in seen_ids:
                            blocks.append(
                                ToolResultBlock(
                                    tool_use_id=tc.tool_use_id,
                                    content="Interrupted by user",
                                    is_error=True,
                                )
                            )
                            seen_ids.add(tc.tool_use_id)
                    state.messages.append(
                        _build_tool_result_message(
                            blocks,
                            assistant_uuid=str(assistant_msg.metadata.get("uuid", "")) if assistant_msg else "",
                        )
                    )
                    context.messages = list(state.messages)
                    context.extras["messages"] = list(state.messages)
                elif tool_calls:
                    assistant_msg = _append_assistant_turn(
                        state.messages, accumulated_text, tool_calls
                    )
                    missing_results = _build_missing_tool_results(
                        tool_calls, "Interrupted by user"
                    )
                    state.messages.append(
                        _build_tool_result_message(
                            missing_results,
                            assistant_uuid=str(assistant_msg.metadata.get("uuid", "")) if assistant_msg else "",
                        )
                    )
                    context.messages = list(state.messages)
                    context.extras["messages"] = list(state.messages)
                    for tc in tool_calls:
                        tre = ToolResultEvent(
                            tool_use_id=tc.tool_use_id,
                            tool_name=tc.tool_name,
                            result="Interrupted by user",
                            is_error=True,
                        )
                        mapped_tre = await _apply_stream_hooks(
                            tre, params.stream_hooks, agent=params.agent
                        )
                        if mapped_tre is not None:
                            yield mapped_tre
                params.messages[:] = state.messages
                return

        except ContextTooLongError as exc:
            stop_breaker.record("context_overflow")
            # Phase 1: try collapse-drain once (TS line 1089-1117)
            # TS: gated on state.transition?.reason !== 'collapse_drain_retry'
            collapse_freed = 0
            if state.last_transition != "collapse_drain_retry":
                messages_for_query, collapse_freed = recover_from_overflow(messages_for_query)
            if collapse_freed > 0:
                # TS: state = { messages: drained.messages, ... }
                state.messages = list(messages_for_query)
                state.auto_compact_tracking = compact_tracker  # TS: autoCompactTracking: tracking
                state.last_transition = "collapse_drain_retry"
                state.transitions.append("collapse_drain_retry")
                state.max_output_tokens_override = None
                state.pending_tool_use_summary = None
                state.stop_hook_active = None
                continue
            # Phase 2: reactive compact — only once (matches TS hasAttemptedReactiveCompact)
            if not state.has_attempted_reactive_compact:
                compacted = await reactive_compact(
                    api_messages,  # TS uses messagesForQuery which is post-collapse
                    params.provider,
                    query_source=params.query_source,
                    transcript_path=transcript_path,
                )
                post_compact = build_post_compact_messages(compacted)
                state.messages = post_compact
                state.has_attempted_reactive_compact = True
                state.auto_compact_tracking = None  # TS: autoCompactTracking: undefined
                state.last_transition = "reactive_compact_retry"
                state.transitions.append("reactive_compact_retry")
                state.max_output_tokens_override = None
                state.pending_tool_use_summary = None
                state.stop_hook_active = None
                continue
            # Phase 3: surface error
            params.messages[:] = state.messages
            yield ErrorEvent(error=f"Prompt too long after compaction: {exc}")
            return

        except Exception as exc:
            error_name = type(exc).__name__
            exc_text = str(exc).lower()

            if "prompt_too_long" in exc_text or "prompt is too long" in exc_text:
                # Same logic as ContextTooLongError above
                collapse_freed = 0
                if state.last_transition != "collapse_drain_retry":
                    messages_for_query, collapse_freed = recover_from_overflow(messages_for_query)
                if collapse_freed > 0:
                    state.messages = list(messages_for_query)
                    state.auto_compact_tracking = compact_tracker  # TS: autoCompactTracking: tracking
                    state.last_transition = "collapse_drain_retry"
                    state.transitions.append("collapse_drain_retry")
                    state.max_output_tokens_override = None
                    state.pending_tool_use_summary = None
                    state.stop_hook_active = None
                    continue
                if not state.has_attempted_reactive_compact:
                    compacted = await reactive_compact(
                        api_messages,  # TS uses messagesForQuery which is post-collapse
                        params.provider,
                        query_source=params.query_source,
                        transcript_path=transcript_path,
                    )
                    post_compact = build_post_compact_messages(compacted)
                    state.messages = post_compact
                    state.has_attempted_reactive_compact = True
                    state.auto_compact_tracking = None  # TS: autoCompactTracking: undefined
                    state.last_transition = "reactive_compact_retry"
                    state.transitions.append("reactive_compact_retry")
                    state.max_output_tokens_override = None
                    state.pending_tool_use_summary = None
                    state.stop_hook_active = None
                    continue
                params.messages[:] = state.messages
                yield ErrorEvent(error=f"Prompt too long after compaction: {exc}")
                return

            if "max_output_tokens" in error_name.lower() or "max_tokens" in exc_text:
                stop_breaker.record("max_output_tokens")
                # TS two-phase recovery:
                # Phase 1: escalate to ESCALATED_MAX_TOKENS (no recovery message, no count bump)
                #   Only if override is not already set and no env override
                import os as _os
                env_max_tokens = _os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
                if (
                    state.max_output_tokens_override is None
                    and not env_max_tokens
                ):
                    # TS: messages = messagesForQuery (same messages, retry with higher limit)
                    state.messages = list(messages_for_query)
                    state.max_output_tokens_override = ESCALATED_MAX_TOKENS
                    state.auto_compact_tracking = compact_tracker  # TS: autoCompactTracking: tracking
                    state.last_transition = "max_output_tokens_escalate"
                    state.transitions.append(
                        f"max_output_tokens_escalate:{state.max_output_tokens_override}"
                    )
                    state.pending_tool_use_summary = None
                    state.stop_hook_active = None
                    continue

                # Phase 2: multi-turn recovery with meta message (TS line 1223-1252)
                # TS: messages = [...messagesForQuery, ...assistantMessages, recoveryMessage]
                if state.max_output_tokens_recovery_count < MAX_RECOVERY:
                    state.messages = list(messages_for_query)
                    if accumulated_text.strip():
                        state.messages.append(assistant_message(accumulated_text.strip()))
                    recovery_msg = user_message(
                        "Output token limit hit. Resume directly — no apology, no recap of "
                        "what you were doing. Pick up mid-thought if that is where the cut "
                        "happened. Break remaining work into smaller pieces.",
                        isMeta=True,
                    )
                    state.messages.append(recovery_msg)
                    state.max_output_tokens_recovery_count += 1
                    state.auto_compact_tracking = compact_tracker  # TS: autoCompactTracking: tracking
                    state.max_output_tokens_override = None
                    state.last_transition = "max_output_tokens_recovery"
                    state.transitions.append("max_output_tokens_recovery")
                    state.pending_tool_use_summary = None
                    state.stop_hook_active = None
                    continue

                # Exhausted — surface error
                params.messages[:] = state.messages
                yield ErrorEvent(
                    error="max_output_tokens recovery exhausted",
                    recoverable=False,
                )
                return

            if fallback_mgr and is_overloaded_error(exc):
                fallback_mgr.record_overloaded()
                can_retry_cleanly = (
                    not accumulated_text.strip()
                    and not tool_calls
                    and turn_usage is None
                )
                if fallback_mgr.should_fallback() and can_retry_cleanly:
                    fb_provider = fallback_mgr.get_fallback_provider()
                    yield ErrorEvent(
                        error=f"Switched to {fb_provider.model_name} due to high demand",
                        recoverable=True,
                    )
                    if hook_runner:
                        await hook_runner.notify(
                            level="warning",
                            message=f"Model fallback: {params.provider.model_name} -> {fb_provider.model_name}",
                        )
                    continue

            if tool_calls:
                assistant_msg = _append_assistant_turn(
                    state.messages,
                    accumulated_text,
                    tool_calls,
                )
                missing_results = _build_missing_tool_results(
                    tool_calls,
                    f"{type(exc).__name__}: {exc}",
                )
                state.messages.append(
                    _build_tool_result_message(
                        missing_results,
                        assistant_uuid=str(assistant_msg.metadata.get("uuid", "")) if assistant_msg else "",
                    )
                )
                context.messages = list(state.messages)
                context.extras["messages"] = list(state.messages)
                for result_block in missing_results:
                    mapped_result = await _apply_stream_hooks(
                        ToolResultEvent(
                            tool_use_id=result_block.tool_use_id,
                            tool_name=_tool_name_for_use_id(tool_calls, result_block.tool_use_id),
                            result=result_block.content,
                            is_error=True,
                        ),
                        params.stream_hooks,
                        agent=params.agent,
                    )
                    if mapped_result is not None:
                        yield mapped_result
            params.messages[:] = state.messages
            yield ErrorEvent(error=f"Provider error: {exc}", recoverable=False)
            return

        # ── Abort check after streaming (TS line 1015-1052) ──
        abort_event = getattr(params.turn_state, "abort_event", None) if params.turn_state else None
        if abort_event is not None and abort_event.is_set():
            if tool_calls:
                assistant_msg = _append_assistant_turn(state.messages, accumulated_text, tool_calls)
                missing_results = _build_missing_tool_results(
                    tool_calls, "Interrupted by user",
                )
                state.messages.append(
                    _build_tool_result_message(
                        missing_results,
                        assistant_uuid=str(assistant_msg.metadata.get("uuid", "")) if assistant_msg else "",
                    )
                )
            params.messages[:] = state.messages
            yield ErrorEvent(error="Interrupted by user", recoverable=False)
            return

        # TS line 1054-1060: yield pending tool use summary from PREVIOUS turn
        # (haiku ~1s resolved during model streaming 5-30s)
        if state.pending_tool_use_summary is not None:
            try:
                summary_result = await state.pending_tool_use_summary
                if summary_result is not None:
                    yield summary_result
            except Exception:
                pass
            state.pending_tool_use_summary = None

        # ── !needsFollowUp branch (TS line 1062-1358) ──
        if not needs_follow_up:
            # Precomputed-only results (denied tools, no real tool calls)
            if precomputed_results:
                denied_reply = accumulated_text.strip()
                denied_blocks: list[TextBlock | ToolUseBlock] = []
                if denied_reply:
                    denied_blocks.append(TextBlock(text=denied_reply))
                for denied_call in denied_tool_calls:
                    denied_blocks.append(
                        ToolUseBlock(
                            id=denied_call.tool_use_id,
                            name=denied_call.tool_name,
                            input=denied_call.tool_input,
                        )
                    )
                state.messages.append(assistant_message(denied_blocks if denied_blocks else denied_reply))
                context.messages = list(state.messages)
                context.extras["messages"] = list(state.messages)
                stop_checker.record_round(
                    reply_text=denied_reply,
                    tool_calls=denied_tool_calls,
                    tool_results=precomputed_results,
                )
                state.messages.append(
                    _build_tool_result_message(
                        precomputed_results,
                        assistant_uuid=str(state.messages[-1].metadata.get("uuid", "")) if state.messages and state.messages[-1].role == "assistant" else "",
                    )
                )
                context.messages = list(state.messages)
                context.extras["messages"] = list(state.messages)
                next_turn_count = state.turn_count + 1
                if params.max_turns and next_turn_count > params.max_turns:
                    params.messages[:] = state.messages
                    yield ErrorEvent(
                        error=f"Reached maximum number of turns ({params.max_turns})",
                        recoverable=False,
                    )
                    return
                # TS resets on normal continue (next_turn)
                state.turn_count = next_turn_count
                state.max_output_tokens_recovery_count = 0
                state.has_attempted_reactive_compact = False
                state.max_output_tokens_override = None
                state.last_transition = "next_turn"
                state.auto_compact_tracking = compact_tracker  # TS: autoCompactTracking: tracking
                # TS: stopHookActive is carried forward (not reset) — line 1724
                output_tokens_escalation.reset()
                params.messages[:] = state.messages
                continue

            # ── Prompt-too-long recovery (TS line 1065-1183) ──
            # In TS, prompt-too-long is withheld during streaming and
            # recovered here. In Python, it's caught as an exception above.
            # The withheld_prompt_too_long flag is set if the provider
            # raised ContextTooLongError (handled in except block).
            # This section handles the case where the error was detected
            # as a stop_reason or last_stop_reason rather than an exception.

            # ── Max output tokens recovery (TS line 1188-1256) ──
            # In TS, max_output_tokens is withheld during streaming.
            # In Python, it's caught as an exception above.
            # This section handles the case where the model returned
            # stop_reason='max_tokens' without raising an exception.
            if last_stop_reason == "max_tokens":
                # Phase 1: escalate to ESCALATED_MAX_TOKENS (TS line 1199-1221)
                import os as _os
                env_max_tokens = _os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
                if (
                    state.max_output_tokens_override is None
                    and not env_max_tokens
                ):
                    state.max_output_tokens_override = ESCALATED_MAX_TOKENS
                    state.auto_compact_tracking = compact_tracker  # TS: autoCompactTracking: tracking
                    state.last_transition = "max_output_tokens_escalate"
                    state.transitions.append(
                        f"max_output_tokens_escalate:{state.max_output_tokens_override}"
                    )
                    state.pending_tool_use_summary = None
                    state.stop_hook_active = None
                    # TS: messages = messagesForQuery (same messages, retry)
                    state.messages = list(messages_for_query)
                    continue

                # Phase 2: multi-turn recovery (TS line 1223-1252)
                # TS: messages = [...messagesForQuery, ...assistantMessages, recoveryMessage]
                if state.max_output_tokens_recovery_count < MAX_RECOVERY:
                    state.messages = list(messages_for_query)
                    if accumulated_text.strip():
                        state.messages.append(assistant_message(accumulated_text.strip()))
                    recovery_msg = user_message(
                        "Output token limit hit. Resume directly — no apology, no recap of "
                        "what you were doing. Pick up mid-thought if that is where the cut "
                        "happened. Break remaining work into smaller pieces.",
                        isMeta=True,
                    )
                    state.messages.append(recovery_msg)
                    state.max_output_tokens_recovery_count += 1
                    state.auto_compact_tracking = compact_tracker  # TS: autoCompactTracking: tracking
                    state.max_output_tokens_override = None
                    state.last_transition = "max_output_tokens_recovery"
                    state.transitions.append("max_output_tokens_recovery")
                    state.pending_tool_use_summary = None
                    state.stop_hook_active = None
                    continue

                # Recovery exhausted — surface the error
                yield ErrorEvent(
                    error="max_output_tokens recovery exhausted",
                    recoverable=False,
                )
                params.messages[:] = state.messages
                return

            # ── Stop hooks (TS line 1258-1306) ──
            stop_checker.record_round(
                reply_text=accumulated_text,
                tool_calls=tool_calls,
            )

            reply = accumulated_text.strip()
            blocks: list[TextBlock | ToolUseBlock] = []
            if reply:
                blocks.append(TextBlock(text=reply))
            state.messages.append(assistant_message(blocks if blocks else reply))
            context.messages = list(state.messages)
            context.extras["messages"] = list(state.messages)

            # TS line 1262-1265: skip stop hooks when last message is an API error
            # (the model never produced a real response — hooks evaluating it
            # create a death spiral: error → hook blocking → retry → error → …)
            if last_stop_reason and last_stop_reason.startswith("error"):
                params.messages[:] = state.messages
                yield CompletionEvent(
                    text=reply,
                    conversation_id=params.conversation_id,
                    usage=turn_usage,
                    stop_reason="api_error",
                )
                return

            # TS: handleStopHooks (line 1267-1306) — check if hooks want to
            # block continuation or inject blocking errors for retry.
            if hook_runner:
                stop_result = await _check_stop_hooks_extended(
                    hook_runner, reply, state.turn_count, agent=params.agent,
                )
                if stop_result == "prevent":
                    # TS: preventContinuation (line 1278-1279)
                    params.messages[:] = state.messages
                    yield CompletionEvent(
                        text=reply,
                        conversation_id=params.conversation_id,
                        usage=turn_usage,
                        stop_reason="stop_hook_prevented",
                    )
                    return
                if isinstance(stop_result, list) and stop_result:
                    # TS line 1282-1306: blocking errors — inject and continue
                    for err_msg in stop_result:
                        state.messages.append(err_msg)
                    state.max_output_tokens_recovery_count = 0
                    state.auto_compact_tracking = compact_tracker  # TS: autoCompactTracking: tracking
                    # TS: preserve hasAttemptedReactiveCompact (line 1297)
                    state.max_output_tokens_override = None
                    state.pending_tool_use_summary = None  # TS: pendingToolUseSummary: undefined
                    state.stop_hook_active = True
                    state.last_transition = "stop_hook_blocking"
                    state.transitions.append("stop_hook_blocking")
                    continue

            # Normal end of turn
            params.messages[:] = state.messages
            yield CompletionEvent(
                text=reply,
                conversation_id=params.conversation_id,
                usage=turn_usage,
                stop_reason=last_stop_reason or "end_turn",
            )
            return

        # ── needsFollowUp = True: tool execution (TS line 1360-1728) ──

        assistant_blocks: list[TextBlock | ToolUseBlock] = []
        if accumulated_text.strip():
            assistant_blocks.append(TextBlock(text=accumulated_text.strip()))
        for tc in tool_calls:
            assistant_blocks.append(ToolUseBlock(
                id=tc.tool_use_id,
                name=tc.tool_name,
                input=tc.tool_input,
            ))
        state.messages.append(assistant_message(assistant_blocks))
        context.messages = list(state.messages)
        context.extras["messages"] = list(state.messages)

        clients = [
            call
            for call in tool_calls
            if isinstance(tool_map.get(call.tool_name), ClientTool)
        ]
        non_client_calls = [
            call
            for call in tool_calls
            if not isinstance(tool_map.get(call.tool_name), ClientTool)
        ]

        results = list(precomputed_results)
        # TS: shouldPreventContinuation (line 1360)
        should_prevent_continuation = False

        if non_client_calls:
            executor = eager_executor or StreamingToolExecutor(tool_map, context)
            for tc in non_client_calls:
                if tc.tool_use_id in eager_started_ids:
                    continue
                executor.add_tool(tc)

            async for update in executor.get_remaining_results():
                if update.new_context is not None:
                    context = update.new_context
                if update.message is None:
                    continue
                # TS: check for hook_stopped_continuation attachment (line 1388-1393)
                if (
                    hasattr(update.message, "metadata")
                    and isinstance(getattr(update.message, "metadata", None), dict)
                    and update.message.metadata.get("type") == "hook_stopped_continuation"
                ):
                    should_prevent_continuation = True
                mapped = await _apply_stream_hooks(update.message, params.stream_hooks, agent=params.agent)
                if mapped is not None:
                    yield mapped

            context = executor.get_updated_context()
            results.extend(executor.get_result_blocks())

        if clients:
            state.pending_client_calls = clients
            if results:
                state.messages.append(
                    _build_tool_result_message(
                        results,
                        assistant_uuid=str(state.messages[-1].metadata.get("uuid", "")) if state.messages and state.messages[-1].role == "assistant" else "",
                    )
                )
            params.messages[:] = state.messages
            yield PendingToolCallEvent(
                run_id=context.turn_id,
                calls=clients,
            )
            return

        if hook_runner:
            results, post_hook_prevented = await _run_post_tool_hooks(
                hook_runner, tool_calls, results, agent=params.agent,
            )
            should_prevent_continuation = should_prevent_continuation or post_hook_prevented

        if params.summary_provider is not None:
            # TS: nextPendingToolUseSummary — store as async task, yielded at
            # the top of the next iteration (not fire-and-forget).
            state.pending_tool_use_summary = _create_tool_use_summary_task(
                params.summary_provider,
                tool_calls, results, accumulated_text,
                params,
            )

        stop_checker.record_round(
            reply_text=accumulated_text,
            tool_calls=tool_calls,
            tool_results=results,
        )

        state.messages.append(
            _build_tool_result_message(
                results,
                assistant_uuid=str(state.messages[-1].metadata.get("uuid", "")) if state.messages and state.messages[-1].role == "assistant" else "",
            )
        )
        context.messages = list(state.messages)
        context.extras["messages"] = list(state.messages)

        # ── Abort check after tool execution (TS line 1485-1516) ──
        abort_event_post = getattr(params.turn_state, "abort_event", None) if params.turn_state else None
        if abort_event_post is not None and abort_event_post.is_set():
            # TS: check maxTurns before returning when aborted (line 1507-1514)
            next_turn_on_abort = state.turn_count + 1
            params.messages[:] = state.messages
            error_text = "Interrupted by user during tool execution"
            if params.max_turns and next_turn_on_abort > params.max_turns:
                error_text = (
                    f"{error_text}. Reached maximum number of turns ({params.max_turns})"
                )
            yield ErrorEvent(error=error_text, recoverable=False)
            return

        # TS: shouldPreventContinuation (line 1519-1521)
        if should_prevent_continuation:
            params.messages[:] = state.messages
            yield CompletionEvent(
                text=accumulated_text.strip(),
                conversation_id=params.conversation_id,
                usage=turn_usage,
                stop_reason="hook_stopped",
            )
            return

        # TS line 1523-1533: only increment when tracking.compacted
        if compact_tracker.compacted:
            compact_tracker.turn_counter += 1

        # max_turns check: TS uses nextTurnCount = turnCount + 1, then nextTurnCount > maxTurns
        next_turn_count = state.turn_count + 1
        if params.max_turns and next_turn_count > params.max_turns:
            params.messages[:] = state.messages
            yield ErrorEvent(
                error=f"Reached maximum number of turns ({params.max_turns})",
                recoverable=False,
            )
            return

        # ── State reset for normal next-turn continue (TS line 1715-1727) ──
        # TS: turnCount: nextTurnCount (incremented here, not at loop top)
        state.turn_count = next_turn_count
        state.max_output_tokens_recovery_count = 0
        state.has_attempted_reactive_compact = False
        state.max_output_tokens_override = None
        state.last_transition = "next_turn"
        # TS: stopHookActive is carried forward (not reset) — line 1724
        # state.stop_hook_active is NOT reset here (TS preserves it)
        # TS: autoCompactTracking: tracking — carry forward the tracker
        state.auto_compact_tracking = compact_tracker
        output_tokens_escalation.reset()

    # Should not reach here (while True), but safety net
    params.messages[:] = state.messages
    yield ErrorEvent(
        error=f"Exceeded max_turns ({params.max_turns})",
        recoverable=False,
    )


def _get_read_file_state() -> dict[str, float]:
    """Fetch file-read tracking lazily to avoid import cycles."""
    try:
        from ..tools.file_read import get_read_file_state

        return get_read_file_state()
    except Exception:
        return {}


def _append_assistant_turn(
    messages: list[Message],
    reply_text: str,
    tool_calls: list[ToolCallEvent],
) -> Message | None:
    """Persist the streamed assistant turn before synthesizing tool results."""
    assistant_blocks: list[TextBlock | ToolUseBlock] = []
    if reply_text.strip():
        assistant_blocks.append(TextBlock(text=reply_text.strip()))
    for tool_call in tool_calls:
        assistant_blocks.append(ToolUseBlock(
            id=tool_call.tool_use_id,
            name=tool_call.tool_name,
            input=tool_call.tool_input,
        ))
    if not assistant_blocks:
        return None
    assistant_msg = assistant_message(assistant_blocks)
    messages.append(assistant_msg)
    return assistant_msg


def _tool_result_message_with_context(
    results: list[ToolResultBlock],
    *,
    assistant_uuid: str = "",
) -> Message:
    metadata: dict[str, Any] = {}
    if results:
        first_content = results[0].content
        metadata["toolUseResult"] = first_content
    if assistant_uuid:
        metadata["sourceToolAssistantUUID"] = assistant_uuid
    return tool_result_message(results) if not metadata else user_message("", **metadata | {"content": ""})


def _build_tool_result_message(
    results: list[ToolResultBlock],
    *,
    assistant_uuid: str = "",
) -> Message:
    metadata: dict[str, Any] = {}
    if results:
        metadata["toolUseResult"] = results[0].content
    if assistant_uuid:
        metadata["sourceToolAssistantUUID"] = assistant_uuid
    return Message(
        role="user",
        content=results,
        metadata={
            "uuid": uuid4().hex,
            "timestamp": __import__("time").time(),
            **metadata,
        },
    )


def _build_missing_tool_results(
    tool_calls: list[ToolCallEvent],
    error_message: str,
) -> list[ToolResultBlock]:
    """Mirror query.ts: every emitted tool_use must receive a tool_result."""
    return [
        ToolResultBlock(
            tool_use_id=tool_call.tool_use_id,
            content=error_message,
            is_error=True,
        )
        for tool_call in tool_calls
    ]


def _tool_name_for_use_id(
    tool_calls: list[ToolCallEvent],
    tool_use_id: str,
) -> str:
    for tool_call in tool_calls:
        if tool_call.tool_use_id == tool_use_id:
            return tool_call.tool_name
    return ""


async def _apply_stream_hooks(
    event: StreamEvent,
    hooks: list[OnStreamEventHook],
    *,
    agent: "Agent | None" = None,
) -> StreamEvent | None:
    """Run stream event hooks, allowing them to modify or suppress events."""
    current: StreamEvent | None = event
    for hook in hooks:
        if current is None:
            break
        current = await hook.on_stream_event(current, agent=agent)  # type: ignore[arg-type]
    return current


async def _check_stop_hooks(
    hook_runner: HookRunner,
    reply_text: str,
    turn: int,
    *,
    agent: "Agent | None" = None,
) -> bool:
    """Run all StopHooks; returns True if any hook says stop."""
    result = await hook_runner.should_stop(
        reply_text=reply_text, turn=turn, agent=agent,  # type: ignore[arg-type]
    )
    return result.should_stop


async def _check_stop_hooks_extended(
    hook_runner: HookRunner,
    reply_text: str,
    turn: int,
    *,
    agent: "Agent | None" = None,
) -> str | list[Message] | None:
    """Run stop hooks with extended result handling (TS handleStopHooks).

    Returns:
      - ``"prevent"`` if hooks say to prevent continuation entirely
      - A list of blocking error messages to inject and retry
      - ``None`` if no hook action needed
    """
    result = await hook_runner.should_stop(
        reply_text=reply_text, turn=turn, agent=agent,  # type: ignore[arg-type]
    )
    if not result.should_stop:
        return None
    if result.blocking_errors:
        return [user_message(err, isMeta=True) for err in result.blocking_errors]
    if result.prevent_continuation:
        return "prevent"
    return None


def _create_tool_use_summary_task(
    provider: BaseProvider,
    tool_calls: list[ToolCallEvent],
    results: list[ToolResultBlock],
    last_assistant_text: str,
    params: QueryParams,
) -> asyncio.Task[ToolUseSummaryEvent | None]:
    """Create tool-use summary as an async task (TS: nextPendingToolUseSummary).

    The task is stored on state.pending_tool_use_summary and awaited at the
    top of the next iteration — matching TS's deferred-yield pattern.
    """
    result_map = {r.tool_use_id: r.content for r in results}
    tools_info = [
        {
            "name": tc.tool_name,
            "input": tc.tool_input,
            "output": result_map.get(tc.tool_use_id, ""),
        }
        for tc in tool_calls
    ]
    tool_use_ids = [tc.tool_use_id for tc in tool_calls]

    async def _run() -> ToolUseSummaryEvent | None:
        try:
            from ..services.tool_use_summary import generate_tool_use_summary

            summary = await generate_tool_use_summary(
                provider=provider,
                tools=tools_info,
                last_assistant_text=last_assistant_text,
            )
            if summary:
                event = ToolUseSummaryEvent(
                    summary=summary,
                    tool_use_ids=tool_use_ids,
                )
                # Also fire callback if present
                if params.on_tool_summary is not None:
                    try:
                        params.on_tool_summary(event)
                    except Exception:
                        pass
                return event
        except Exception:
            pass
        return None

    return asyncio.ensure_future(_run())


def _fire_tool_use_summary(
    provider: BaseProvider,
    tool_calls: list[ToolCallEvent],
    results: list[ToolResultBlock],
    last_assistant_text: str,
    params: QueryParams,
) -> None:
    """Fire tool-use summary generation as background task (non-blocking).

    When complete, the summary is delivered via ``params.on_tool_summary``
    callback (typically ``Agent._fire_event``).
    """
    import asyncio

    result_map = {r.tool_use_id: r.content for r in results}
    tools_info = [
        {
            "name": tc.tool_name,
            "input": tc.tool_input,
            "output": result_map.get(tc.tool_use_id, ""),
        }
        for tc in tool_calls
    ]
    tool_use_ids = [tc.tool_use_id for tc in tool_calls]

    async def _run() -> None:
        from ..services.tool_use_summary import generate_tool_use_summary

        summary = await generate_tool_use_summary(
            provider=provider,
            tools=tools_info,
            last_assistant_text=last_assistant_text,
        )
        if summary and params.on_tool_summary is not None:
            event = ToolUseSummaryEvent(
                summary=summary,
                tool_use_ids=tool_use_ids,
            )
            try:
                params.on_tool_summary(event)
            except Exception:
                pass

    try:
        asyncio.ensure_future(_run())
    except RuntimeError:
        pass


def _fire_post_sampling(
    hook_runner: HookRunner,
    messages: list[Message],
    system_text: str | list[Any],
    reply_text: str,
    tool_calls: list[ToolCallEvent],
    *,
    agent: "Agent | None" = None,
) -> None:
    """Fire post-sampling hooks as a background task (fire-and-forget)."""
    import asyncio

    sp = system_text if isinstance(system_text, str) else "<structured>"
    ctx = PostSamplingContext(
        messages=list(messages),
        system_prompt=sp,
        reply_text=reply_text,
        tool_calls=list(tool_calls),
    )

    async def _run() -> None:
        await hook_runner.run_post_sampling(ctx, agent=agent)  # type: ignore[arg-type]

    try:
        asyncio.ensure_future(_run())
    except RuntimeError:
        pass


async def _run_post_tool_hooks(
    hook_runner: HookRunner,
    tool_calls: list[ToolCallEvent],
    results: list[ToolResultBlock],
    *,
    agent: "Agent | None" = None,
) -> tuple[list[ToolResultBlock], bool]:
    """Run PostToolUseHooks, allowing them to modify results."""
    call_map = {tc.tool_use_id: tc for tc in tool_calls}
    final: list[ToolResultBlock] = []
    should_prevent_continuation = False
    for result_block in results:
        tc = call_map.get(result_block.tool_use_id)
        if tc is not None:
            as_event = ToolResultEvent(
                tool_use_id=result_block.tool_use_id,
                tool_name=tc.tool_name,
                result=result_block.content,
                is_error=result_block.is_error,
            )
            modified_event = await hook_runner.run_post_tool_use(
                tc, as_event, agent=agent,  # type: ignore[arg-type]
            )
            if modified_event.metadata.get("type") == "hook_stopped_continuation":
                should_prevent_continuation = True
            final.append(ToolResultBlock(
                tool_use_id=modified_event.tool_use_id,
                content=modified_event.result,
                is_error=modified_event.is_error,
            ))
        else:
            final.append(result_block)
    return final, should_prevent_continuation
