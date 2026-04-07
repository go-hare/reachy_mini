"""Query turn orchestration for a single conversation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
import os
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..delegation.coordinator import get_coordinator_user_context
from ..engine.input_processor import (
    InputProcessor,
    ProcessedInput,
    create_image_metadata_text,
    process_attachments,
)
from ..hooks.user_scripts import HookEvent as UserHookEvent, HookInput, UserHookRunner
from ..messages import (
    CompletionEvent,
    DocumentBlock,
    ErrorEvent,
    ImageBlock,
    Message,
    PendingToolCallEvent,
    StreamEvent,
    TextBlock,
    ToolCallEvent,
    ToolResultBlock,
    ToolResultEvent,
    UsageEvent,
)
from ..prompts import SystemPrompt
from ..providers import BaseProvider, ProviderConfig, create_provider
from ..usage import UsageRecord
from .query import QueryParams, _build_tool_result_message, query as run_query

if TYPE_CHECKING:
    from ..agent import Agent
    from ..tool import Tool


@dataclass(slots=True)
class PreparedTurn:
    query_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    active_tools: list["Tool"] = field(default_factory=list)
    local_result: str | None = None
    local_error: str | None = None
    message_content: list[Any] | str = ""
    accepted_speculation: Any | None = None
    attachments: list[Any] = field(default_factory=list)
    processed_input: ProcessedInput | None = None
    model_override: str = ""
    effort_override: str = ""
    input_mode: str = "prompt"
    bridge_origin: bool = False
    skip_slash_commands: bool = False


class TurnPhase(str, Enum):
    PREPARED = "prepared"
    ACCEPTED = "accepted"
    PENDING_CLIENT = "pending_client"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class TurnState:
    conversation_id: str
    turn_id: str = field(default_factory=lambda: uuid4().hex[:16])
    query_text: str = ""
    phase: TurnPhase = TurnPhase.PREPARED
    pending_run_id: str = ""
    stop_reason: str = ""
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)


class QueryEngine:
    """Own the per-turn orchestration around the core query loop.

    Mirrors TS QueryEngine: one instance per conversation, persists
    state (messages, usage, turn count) across submitMessage calls.
    """

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent
        # TS: totalUsage — accumulated across all turns in this engine
        self._total_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        # TS: turnCount — incremented on each user message within a submitMessage
        self._turn_count: int = 0
        # TS: lastStopReason — captured from CompletionEvent and stream events
        self._last_stop_reason: str | None = None
        # TS: startTime — set at the beginning of each submitMessage
        self._start_time: float = 0.0

    async def submit_message(
        self,
        user_text: str,
        *,
        conversation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        attachments: list[Any] | None = None,
        user_id: str = "",
        turn_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        # TS: startTime = Date.now()
        self._start_time = time.time()
        # TS: turnCount = 1 at start of each submitMessage
        self._turn_count = 1
        # TS: lastStopReason reset per submitMessage
        self._last_stop_reason = None

        turn_state = TurnState(
            conversation_id=conversation_id or self._agent._conversation_id,
            turn_id=turn_id or uuid4().hex[:16],
        )
        self._agent._current_turn_state = turn_state
        prepared = await self._prepare_turn(
            user_text,
            metadata=metadata,
            attachments=attachments,
            user_id=user_id,
        )

        if prepared.local_error is not None:
            turn_state.phase = TurnPhase.FAILED
            yield ErrorEvent(error=prepared.local_error, recoverable=True)
            return

        if prepared.local_result is not None:
            turn_state.phase = TurnPhase.COMPLETED
            yield CompletionEvent(
                text=prepared.local_result,
                conversation_id=conversation_id or self._agent._conversation_id,
            )
            return

        if prepared.accepted_speculation is not None:
            async for event in self._replay_accepted_speculation(
                prepared,
                conversation_id=conversation_id,
            ):
                yield event
            return

        budget = self._agent.budget_status
        if budget is not None and budget.is_over_limit():
            turn_state.phase = TurnPhase.FAILED
            yield ErrorEvent(
                error=f"Token budget exceeded: {budget.status_text()}",
                recoverable=False,
            )
            return

        turn_state.query_text = prepared.query_text
        async for event in self._run_prepared_turn(
            prepared,
            conversation_id=conversation_id,
        ):
            yield event

    async def continue_with_tool_results(
        self,
        run_id: str,
        results: list[ToolResultBlock],
    ) -> AsyncGenerator[StreamEvent, None]:
        if self._agent._pending_client_run_id is None or not self._agent._pending_client_calls:
            raise RuntimeError("No client-side tool call is currently pending for this agent")
        if run_id != self._agent._pending_client_run_id:
            raise RuntimeError(
                f"Mismatched pending tool run_id: expected {self._agent._pending_client_run_id}, got {run_id}"
            )

        expected_ids = {call.tool_use_id for call in self._agent._pending_client_calls}
        actual_ids = {result.tool_use_id for result in results}
        if actual_ids != expected_ids:
            raise RuntimeError(
                "Submitted tool results do not match the pending client-side tool calls"
            )

        last = self._agent._messages[-1] if self._agent._messages else None
        assistant_uuid = ""
        if last is not None and last.role == "assistant":
            assistant_uuid = str(last.metadata.get("uuid", "")) if last.metadata else ""
        self._agent._messages.append(
            _build_tool_result_message(results, assistant_uuid=assistant_uuid)
        )
        self._agent._pending_client_run_id = None
        self._agent._pending_client_calls = []
        self._agent._persist_session_snapshot()

        current_turn = getattr(self._agent, "_current_turn_state", None)
        if current_turn is not None:
            current_turn.phase = TurnPhase.ACCEPTED
            current_turn.pending_run_id = ""

        budget = self._agent.budget_status
        if budget is not None and budget.is_over_limit():
            if current_turn is not None:
                current_turn.phase = TurnPhase.FAILED
            yield ErrorEvent(
                error=f"Token budget exceeded: {budget.status_text()}",
                recoverable=False,
            )
            return

        params = self._build_query_params(
            tools=list(getattr(self._agent, "_current_turn_tools", self._agent._tools)),
            conversation_id=self._agent._conversation_id,
        )

        reply_text = ""
        stop_reason = ""
        saw_error = False
        async for event in run_query(params):
            self._handle_event(event)
            if isinstance(event, CompletionEvent):
                reply_text = event.text
                stop_reason = event.stop_reason or ""
            elif isinstance(event, ErrorEvent):
                saw_error = True
            # TS: turnCount++ when user messages appear (tool result turns)
            if isinstance(event, ToolResultEvent):
                self._turn_count += 1
            yield event

            # TS: check maxBudgetUsd after every yielded message
            budget = self._agent.budget_status
            if budget is not None and budget.is_over_limit():
                self._agent._messages = params.messages
                if current_turn is not None:
                    current_turn.phase = TurnPhase.FAILED
                    current_turn.stop_reason = "budget_exceeded"
                yield ErrorEvent(
                    error=f"Budget exceeded: {budget.status_text()}",
                    recoverable=False,
                )
                self._agent._persist_session_snapshot()
                return

        self._agent._messages = params.messages
        if self._agent._pending_client_run_id is not None:
            self._agent._persist_session_snapshot()
            if current_turn is not None:
                current_turn.phase = TurnPhase.PENDING_CLIENT
                current_turn.pending_run_id = self._agent._pending_client_run_id or ""
            return

        if saw_error:
            if current_turn is not None:
                current_turn.phase = TurnPhase.FAILED
                current_turn.stop_reason = stop_reason or "error"
            self._agent._persist_session_snapshot()
            return

        await self._agent._hook_runner.run_post_query(
            user_text=self._agent._current_turn_user_text,
            reply=reply_text,
            agent=self._agent,
        )
        if current_turn is not None:
            current_turn.phase = TurnPhase.COMPLETED
            current_turn.stop_reason = stop_reason
        if reply_text:
            self._agent._record_runtime_memory(
                user_text=self._agent._current_turn_user_text,
                reply_text=reply_text,
                had_tools=bool(self._agent._last_turn_tool_names),
            )
        self._agent._persist_session_snapshot()

    async def _prepare_turn(
        self,
        user_text: str,
        *,
        metadata: dict[str, Any] | None = None,
        attachments: list[Any] | None = None,
        user_id: str = "",
    ) -> PreparedTurn:
        host_metadata = dict(metadata or {})
        if user_id:
            host_metadata.setdefault("user_id", user_id)
        input_mode = str(host_metadata.get("input_mode", "prompt") or "prompt").strip() or "prompt"
        bridge_origin = bool(host_metadata.get("bridge_origin", False))
        skip_slash_commands = bool(
            host_metadata.get("skip_slash_commands", bridge_origin)
        )
        try:
            from ..services.prompt_suggestion import (
                abort_prompt_suggestion,
                abort_speculation,
                try_accept_speculation,
            )

            accepted_speculation = try_accept_speculation(
                user_text,
                agent=self._agent,
            )
            if accepted_speculation is not None:
                return PreparedTurn(
                    query_text=user_text,
                    metadata=host_metadata,
                    active_tools=list(self._agent._tools),
                    message_content=await self._build_user_message_content(
                        user_text,
                        attachments=attachments,
                        input_mode=input_mode,
                        bridge_origin=bridge_origin,
                        skip_slash_commands=skip_slash_commands,
                    ),
                    accepted_speculation=accepted_speculation,
                    attachments=list(attachments or []),
                    input_mode=input_mode,
                    bridge_origin=bridge_origin,
                    skip_slash_commands=skip_slash_commands,
                )

            abort_prompt_suggestion(self._agent)
            abort_speculation(self._agent)
        except Exception:
            pass

        prepared = PreparedTurn(
            active_tools=list(self._agent._tools),
            metadata=host_metadata,
            attachments=list(attachments or []),
            input_mode=input_mode,
            bridge_origin=bridge_origin,
            skip_slash_commands=skip_slash_commands,
        )

        processor = InputProcessor(
            cwd=self._get_working_directory(),
            command_registry=self._agent._command_registry,
        )
        processed = await processor.process(
            user_text,
            mode=input_mode,
            skip_slash_commands=skip_slash_commands,
            bridge_origin=bridge_origin,
        )
        prepared.processed_input = processed

        if not processed.should_query:
            if processed.command_type == "local":
                parsed = self._agent._command_registry.parse(user_text)
                if parsed is None:
                    prepared.local_result = processed.result_text or processed.command_output
                    return prepared
                command, args = parsed
                prepared.local_result = await command.execute(args, self._agent)
                return prepared

            prepared.local_result = (
                processed.result_text
                or processed.command_output
                or f"Skipped input: {user_text.strip()}"
            )
            return prepared

        prepared.query_text = processed.prompt_text or user_text
        prepared.active_tools = self._filter_tools_for_command(processed.allowed_tools)
        prepared.model_override = processed.model_override
        prepared.effort_override = processed.effort_override

        if processed.command_name:
            prepared.metadata = {
                **host_metadata,
                "command_name": processed.command_name,
                "command_args": processed.command_args,
                "original_text": processed.command_output or user_text.strip(),
            }

        hook_error, hook_added_attachments, updated_query_text = await self._apply_user_prompt_submit_hooks(
            query_text=prepared.query_text,
            original_text=user_text,
            bridge_origin=bridge_origin,
        )
        if hook_error is not None:
            prepared.local_error = hook_error
            return prepared
        if hook_added_attachments:
            prepared.attachments.extend(hook_added_attachments)
        if updated_query_text and updated_query_text != prepared.query_text:
            prepared.query_text = updated_query_text
            prepared.processed_input = await processor.process(
                updated_query_text,
                mode=input_mode,
                skip_slash_commands=skip_slash_commands,
                bridge_origin=bridge_origin,
            )
            if not prepared.processed_input.should_query:
                prepared.local_result = (
                    prepared.processed_input.result_text
                    or prepared.processed_input.command_output
                    or updated_query_text
                )
                return prepared
            if prepared.processed_input.allowed_tools:
                prepared.active_tools = self._filter_tools_for_command(
                    prepared.processed_input.allowed_tools
                )
            if prepared.processed_input.model_override:
                prepared.model_override = prepared.processed_input.model_override
            if prepared.processed_input.effort_override:
                prepared.effort_override = prepared.processed_input.effort_override

        prepared.message_content = await self._build_user_message_content(
            prepared.query_text,
            processed=prepared.processed_input,
            attachments=prepared.attachments,
            input_mode=input_mode,
            bridge_origin=bridge_origin,
            skip_slash_commands=skip_slash_commands,
        )
        return prepared

    async def _replay_accepted_speculation(
        self,
        prepared: PreparedTurn,
        *,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        accepted = prepared.accepted_speculation
        assert accepted is not None
        from ..services.prompt_suggestion import SpeculationState, _cleanup_speculation_overlay, _commit_speculation_overlay

        overlay_state = SpeculationState(
            workspace_root=accepted.workspace_root,
            overlay_dir=accepted.overlay_dir,
            written_paths=list(accepted.written_paths),
        )

        try:
            _commit_speculation_overlay(overlay_state)

            current_turn = getattr(self._agent, "_current_turn_state", None)
            user_metadata = dict(prepared.metadata)
            user_metadata.setdefault("uuid", uuid4().hex)
            user_metadata.setdefault("timestamp", time.time())
            if current_turn is not None:
                user_metadata.setdefault("conversation_id", current_turn.conversation_id)
                user_metadata.setdefault("turn_id", current_turn.turn_id)
            self._agent._messages.append(
                Message(role="user", content=prepared.message_content, metadata=user_metadata)
            )
            self._agent._current_turn_user_text = prepared.query_text
            self._agent._last_turn_tool_names = []
            self._agent._current_turn_tools = list(prepared.active_tools)

            if current_turn is not None:
                current_turn.phase = TurnPhase.ACCEPTED
                current_turn.query_text = prepared.query_text

            reply_text = ""
            stop_reason = "speculation_accept"
            saw_error = False
            for message in accepted.added_messages:
                self._agent._messages.append(message)

            for event in accepted.events:
                self._handle_event(event)
                if isinstance(event, CompletionEvent):
                    reply_text = event.text
                    stop_reason = event.stop_reason or stop_reason
                elif isinstance(event, ErrorEvent):
                    saw_error = True
                elif isinstance(event, ToolResultEvent):
                    self._turn_count += 1
                yield event

            if not reply_text:
                reply_text = accepted.reply

            if saw_error:
                if current_turn is not None:
                    current_turn.phase = TurnPhase.FAILED
                    current_turn.stop_reason = stop_reason or "error"
                self._agent._persist_session_snapshot()
                return

            if accepted.needs_continuation:
                self._agent._persist_session_snapshot()
                params = self._build_query_params(
                    tools=prepared.active_tools,
                    conversation_id=conversation_id or self._agent._conversation_id,
                )
                reply_text = ""
                stop_reason = ""
                saw_error = False
                async for event in run_query(params):
                    self._handle_event(event)
                    if isinstance(event, CompletionEvent):
                        reply_text = event.text
                        stop_reason = event.stop_reason or ""
                    elif isinstance(event, ErrorEvent):
                        saw_error = True
                    if isinstance(event, ToolResultEvent):
                        self._turn_count += 1
                    yield event

                    budget = self._agent.budget_status
                    if budget is not None and budget.is_over_limit():
                        self._agent._messages = params.messages
                        if current_turn is not None:
                            current_turn.phase = TurnPhase.FAILED
                            current_turn.stop_reason = "budget_exceeded"
                        yield ErrorEvent(
                            error=f"Budget exceeded: {budget.status_text()}",
                            recoverable=False,
                        )
                        self._agent._persist_session_snapshot()
                        return

                self._agent._messages = params.messages
                if self._agent._pending_client_run_id is not None:
                    self._agent._persist_session_snapshot()
                    if current_turn is not None:
                        current_turn.phase = TurnPhase.PENDING_CLIENT
                        current_turn.pending_run_id = self._agent._pending_client_run_id or ""
                    return

                if saw_error:
                    if current_turn is not None:
                        current_turn.phase = TurnPhase.FAILED
                        current_turn.stop_reason = stop_reason or "error"
                    self._agent._persist_session_snapshot()
                    return

            await self._agent._hook_runner.run_post_query(
                user_text=prepared.query_text,
                reply=reply_text,
                agent=self._agent,
            )
            if current_turn is not None:
                current_turn.phase = TurnPhase.COMPLETED
                current_turn.stop_reason = stop_reason
            if reply_text:
                self._agent._record_runtime_memory(
                    user_text=prepared.query_text,
                    reply_text=reply_text,
                    had_tools=bool(self._agent._last_turn_tool_names),
                )
            self._agent._persist_session_snapshot()
        finally:
            _cleanup_speculation_overlay(overlay_state)

    async def _run_prepared_turn(
        self,
        prepared: PreparedTurn,
        *,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        conv_id = conversation_id or self._agent._conversation_id

        await self._agent._hook_runner.run_pre_query(
            user_text=prepared.query_text,
            messages=self._agent._messages,
            agent=self._agent,
        )

        prefetch_handle = None
        if self._agent._skill_prefetch is not None:
            prefetch_handle = self._agent._skill_prefetch.start(
                prepared.query_text,
                self._agent._messages,
            )

        current_turn = getattr(self._agent, "_current_turn_state", None)
        user_metadata = dict(prepared.metadata)
        user_metadata.setdefault("uuid", uuid4().hex)
        user_metadata.setdefault("timestamp", time.time())
        if current_turn is not None:
            user_metadata.setdefault("conversation_id", current_turn.conversation_id)
            user_metadata.setdefault("turn_id", current_turn.turn_id)
        self._agent._messages.append(
            Message(role="user", content=prepared.message_content, metadata=user_metadata)
        )
        self._agent._current_turn_user_text = prepared.query_text
        self._agent._last_turn_tool_names = []
        self._agent._current_turn_tools = list(prepared.active_tools)

        if current_turn is not None:
            current_turn.phase = TurnPhase.ACCEPTED

        if prefetch_handle is not None:
            attachments = await prefetch_handle.collect()
            if attachments and self._agent._attachment_collector is not None:
                self._agent._messages = self._agent._attachment_collector.inject(
                    attachments,
                    self._agent._messages,
                )

        self._agent._persist_session_snapshot()

        params = self._build_query_params(
            tools=prepared.active_tools,
            conversation_id=conv_id,
            model_override=prepared.model_override,
            effort_override=prepared.effort_override,
        )

        reply_text = ""
        stop_reason = ""
        saw_error = False
        async for event in run_query(params):
            self._handle_event(event)
            if isinstance(event, CompletionEvent):
                reply_text = event.text
                stop_reason = event.stop_reason or ""
            elif isinstance(event, ErrorEvent):
                saw_error = True
            # TS: turnCount++ when message.type === 'user' (tool result turns).
            # In Python query(), ToolResultEvent is yielded for each tool
            # result — equivalent to the TS user-message increment.
            if isinstance(event, ToolResultEvent):
                self._turn_count += 1
            yield event

            # TS: check maxBudgetUsd after every yielded message
            budget = self._agent.budget_status
            if budget is not None and budget.is_over_limit():
                self._agent._messages = params.messages
                if current_turn is not None:
                    current_turn.phase = TurnPhase.FAILED
                    current_turn.stop_reason = "budget_exceeded"
                yield ErrorEvent(
                    error=f"Budget exceeded: {budget.status_text()}",
                    recoverable=False,
                )
                self._agent._persist_session_snapshot()
                return

        self._agent._messages = params.messages

        if self._agent._pending_client_run_id is not None:
            self._agent._persist_session_snapshot()
            if current_turn is not None:
                current_turn.phase = TurnPhase.PENDING_CLIENT
                current_turn.pending_run_id = self._agent._pending_client_run_id or ""
            return

        if saw_error:
            if current_turn is not None:
                current_turn.phase = TurnPhase.FAILED
                current_turn.stop_reason = stop_reason or "error"
            self._agent._persist_session_snapshot()
            return

        await self._agent._hook_runner.run_post_query(
            user_text=prepared.query_text,
            reply=reply_text,
            agent=self._agent,
        )
        if current_turn is not None:
            current_turn.phase = TurnPhase.COMPLETED
            current_turn.stop_reason = stop_reason
        if reply_text:
            self._agent._record_runtime_memory(
                user_text=prepared.query_text,
                reply_text=reply_text,
                had_tools=bool(self._agent._last_turn_tool_names),
            )
        self._agent._persist_session_snapshot()

    def _build_query_params(
        self,
        *,
        tools: list["Tool"],
        conversation_id: str,
        model_override: str = "",
        effort_override: str = "",
    ) -> QueryParams:
        query_source = (
            "sdk"
            if bool(getattr(self._agent, "_runtime_is_non_interactive", False))
            else "repl_main_thread"
        )
        user_context, system_context = self._fetch_context_parts(
            conversation_id=conversation_id,
        )
        system_prompt = self._compose_system_prompt(
            conversation_id=conversation_id,
        )
        provider = self._resolve_provider_for_turn(
            model_override=model_override,
            effort_override=effort_override,
        )
        return QueryParams(
            provider=provider,
            system_prompt=system_prompt,
            messages=self._agent._messages,
            tools=tools,
            agent=self._agent,
            conversation_id=conversation_id,
            agent_id=self._agent._agent_id,
            max_turns=self._agent._config.max_turns,
            compact_threshold=self._agent._config.compact_threshold,
            compact_config=getattr(self._agent, "_compact_config", None),
            compact_tracker=getattr(self._agent, "_compact_tracker", None),
            snip_config=getattr(self._agent, "_snip_config", None),
            collapse_config=getattr(self._agent, "_collapse_config", None),
            pre_hooks=self._agent._hook_runner.pre_query,
            post_hooks=self._agent._hook_runner.post_query,
            stream_hooks=self._agent._hook_runner.stream_event,
            permission_checker=self._agent._permission_checker,
            attachment_collector=self._agent._attachment_collector,
            hook_runner=self._agent._hook_runner,
            fallback_config=self._agent._fallback_config,
            summary_provider=self._agent._summary_provider,
            on_tool_summary=self._agent._fire_event,
            session_memory_content=self._get_session_memory_content(),
            turn_state=getattr(self._agent, "_current_turn_state", None),
            working_directory=self._get_working_directory(),
            query_source=query_source,
            is_non_interactive=bool(getattr(self._agent, "_runtime_is_non_interactive", False)),
            user_context=user_context,
            system_context=system_context,
            task_budget=getattr(self._agent, "_task_budget", None),
        )

    def _handle_event(self, event: StreamEvent) -> None:
        if isinstance(event, PendingToolCallEvent):
            self._agent._pending_client_run_id = event.run_id
            self._agent._pending_client_calls = list(event.calls)
            return

        # TS: message_stop — accumulate per-API-call usage into totalUsage.
        # UsageEvent is emitted at the end of each LLM streaming response
        # (equivalent to TS message_stop). We must accumulate here because
        # a single query() run may make multiple API calls (tool loops).
        # CompletionEvent only carries the LAST call's usage.
        if isinstance(event, UsageEvent):
            usage = UsageRecord(
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                cache_read_tokens=event.cache_read_tokens,
                cache_creation_tokens=event.cache_creation_tokens,
                model=event.model,
            )
            self._agent._usage_tracker.add(usage)
            self._agent._stats_tracker.record_request(
                model=event.model or self._agent._provider.model_name,
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                cache_read_tokens=event.cache_read_tokens,
                cache_write_tokens=event.cache_creation_tokens,
            )
            self._total_usage["input_tokens"] += event.input_tokens
            self._total_usage["output_tokens"] += event.output_tokens
            self._total_usage["cache_read_tokens"] += event.cache_read_tokens
            self._total_usage["cache_creation_tokens"] += event.cache_creation_tokens
            # TS: capture stop_reason from message_delta (arrives before
            # message_stop). In Python, UsageEvent carries the stop_reason
            # from the stream — equivalent to TS message_delta.stop_reason.
            if event.stop_reason:
                self._last_stop_reason = event.stop_reason
            tb = getattr(self._agent, "_task_budget", None)
            if tb is not None:
                from ..services.task_budget import apply_usage_to_task_budget

                self._agent._task_budget = apply_usage_to_task_budget(
                    tb,
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                )
            return

        if isinstance(event, CompletionEvent):
            self._agent._pending_client_run_id = None
            self._agent._pending_client_calls = []
            current_turn = getattr(self._agent, "_current_turn_state", None)
            if current_turn is not None and event.stop_reason:
                current_turn.stop_reason = event.stop_reason
            # TS: capture lastStopReason from completion
            if event.stop_reason:
                self._last_stop_reason = event.stop_reason
            # Usage from CompletionEvent is NOT re-accumulated here — it was
            # already counted via UsageEvent above (TS message_stop).
            return

        if isinstance(event, ToolCallEvent):
            self._agent._last_turn_tool_names.append(event.tool_name)
            self._agent._stats_tracker.record_tool_call(event.tool_name)
            return

    def _resolve_provider_for_turn(
        self,
        *,
        model_override: str = "",
        effort_override: str = "",
    ) -> BaseProvider:
        provider = self._agent._provider
        if not model_override and not effort_override:
            return provider

        config = getattr(provider, "_config", None)
        if not isinstance(config, ProviderConfig):
            return provider

        extras = dict(config.extras or {})
        if effort_override:
            extras["reasoning_effort"] = effort_override

        cloned_config = ProviderConfig(
            type=config.type,
            model=model_override or config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            retry=config.retry,
            enable_cache=config.enable_cache,
            extras=extras,
        )
        return create_provider(cloned_config)

    def _filter_tools_for_command(self, allowed_tools: list[str] | None) -> list["Tool"]:
        if not allowed_tools:
            return list(self._agent._tools)
        allowed = set(allowed_tools)
        return [tool for tool in self._agent._tools if tool.name in allowed]

    async def _build_user_message_content(
        self,
        raw_text: str,
        *,
        processed: ProcessedInput | None = None,
        attachments: list[Any] | None = None,
        input_mode: str = "prompt",
        bridge_origin: bool = False,
        skip_slash_commands: bool = False,
    ) -> list[Any] | str:
        user_payload = raw_text
        if processed is None and raw_text.strip():
            processor = InputProcessor(
                cwd=self._get_working_directory(),
                command_registry=self._agent._command_registry,
            )
            processed = await processor.process(
                raw_text,
                mode=input_mode,
                skip_slash_commands=skip_slash_commands,
                bridge_origin=bridge_origin,
            )
        if processed is not None and processed.should_query and processed.messages:
            user_payload = processed.messages[0].get("content", raw_text)
        blocks: list[Any] = []
        deferred_attachments: list[dict[str, Any]] = []

        if isinstance(user_payload, str):
            if user_payload:
                blocks.append(TextBlock(text=user_payload))
        else:
            for block in user_payload:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    blocks.append(TextBlock(text=str(block.get("text", ""))))
                elif block_type == "image":
                    source = block.get("source")
                    if isinstance(source, dict) and source.get("type") == "base64":
                        blocks.append(
                            ImageBlock(
                                source=str(source.get("data", "")),
                                media_type=str(source.get("media_type", "image/png")),
                            )
                        )
                    elif block.get("path"):
                        deferred_attachments.append({
                            "type": "image",
                            "path": str(block.get("path", "")),
                        })
                elif block_type == "document":
                    source = block.get("source")
                    if isinstance(source, dict) and source.get("type") == "base64":
                        blocks.append(
                            DocumentBlock(
                                source=str(source.get("data", "")),
                                media_type=str(source.get("media_type", "application/pdf")),
                            )
                        )

        host_blocks, host_deferred = self._build_attachment_blocks(attachments or [])
        blocks.extend(host_blocks)
        deferred_attachments.extend(host_deferred)

        if deferred_attachments:
            for attachment in process_attachments(
                deferred_attachments,
                cwd=self._get_working_directory(),
            ):
                if (
                    attachment.attachment_type == "image"
                    and attachment.metadata
                ):
                    metadata_text = create_image_metadata_text(attachment.metadata)
                    if metadata_text:
                        blocks.append(TextBlock(text=metadata_text))
                content = attachment.content
                if isinstance(content, str):
                    blocks.append(TextBlock(text=content))
                elif isinstance(content, dict):
                    content_type = content.get("type", "")
                    if content_type == "text":
                        blocks.append(TextBlock(text=str(content.get("text", ""))))
                    elif content_type == "image":
                        source = content.get("source", {})
                        if isinstance(source, dict) and source.get("type") == "base64":
                            blocks.append(
                                ImageBlock(
                                    source=str(source.get("data", "")),
                                    media_type=str(source.get("media_type", "image/png")),
                                )
                            )
                        else:
                            blocks.append(TextBlock(text=str(content)))
                    elif content_type == "document":
                        source = content.get("source", {})
                        if isinstance(source, dict) and source.get("type") == "base64":
                            blocks.append(
                                DocumentBlock(
                                    source=str(source.get("data", "")),
                                    media_type=str(source.get("media_type", "application/pdf")),
                                )
                            )
                        else:
                            blocks.append(TextBlock(text=str(content)))
                    else:
                        blocks.append(TextBlock(text=str(content)))

        if not blocks:
            return raw_text
        if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
            return blocks[0].text
        return blocks

    async def _apply_user_prompt_submit_hooks(
        self,
        *,
        query_text: str,
        original_text: str,
        bridge_origin: bool,
    ) -> tuple[str | None, list[dict[str, Any]], str]:
        runner = UserHookRunner(
            cwd=self._get_working_directory(),
            session_id=self._agent._conversation_id,
        )
        if not runner.has_hooks(UserHookEvent.USER_PROMPT_SUBMIT):
            return None, [], query_text

        output = await runner.fire(
            UserHookEvent.USER_PROMPT_SUBMIT,
            HookInput(
                session_id=self._agent._conversation_id,
                cwd=self._get_working_directory(),
                extra={
                    "prompt": query_text,
                    "original_text": original_text,
                    "bridge_origin": bridge_origin,
                },
            ),
        )

        if output.blocking or not output.should_continue:
            message = (
                output.system_message
                or output.reason
                or output.stop_reason
                or "Operation stopped by UserPromptSubmit hook."
            )
            return message, [], query_text

        added_attachments: list[dict[str, Any]] = []
        if output.additional_context:
            added_attachments.append(
                {
                    "type": "text",
                    "text": output.additional_context,
                }
            )

        updated_query_text = query_text
        if output.updated_input is not None:
            extracted = self._extract_updated_input_text(output.updated_input)
            if extracted:
                updated_query_text = extracted
        elif output.initial_user_message:
            updated_query_text = output.initial_user_message

        return None, added_attachments, updated_query_text

    @staticmethod
    def _extract_updated_input_text(updated_input: Any) -> str:
        if isinstance(updated_input, str):
            return updated_input.strip()
        if isinstance(updated_input, dict):
            for key in ("text", "prompt", "message", "content"):
                value = updated_input.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def _build_attachment_blocks(
        self,
        attachments: list[Any],
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        blocks: list[Any] = []
        deferred: list[dict[str, Any]] = []

        for attachment in attachments:
            if isinstance(attachment, (TextBlock, ImageBlock, DocumentBlock)):
                blocks.append(attachment)
                continue

            if all(hasattr(attachment, field) for field in ("type", "content", "metadata")):
                att_type = str(getattr(attachment, "type", "") or "").strip()
                content = str(getattr(attachment, "content", "") or "")
                metadata = getattr(attachment, "metadata", {}) or {}
                if att_type == "image" and isinstance(metadata, dict) and metadata.get("path"):
                    deferred.append({
                        "type": "image",
                        "path": str(metadata.get("path", "")),
                    })
                elif att_type == "file" and isinstance(metadata, dict) and metadata.get("path"):
                    deferred.append({
                        "type": "file",
                        "path": str(metadata.get("path", "")),
                    })
                elif content:
                    blocks.append(TextBlock(text=content))
                continue

            if not isinstance(attachment, dict):
                blocks.append(TextBlock(text=str(attachment)))
                continue

            att_type = str(attachment.get("type", "") or "").strip()
            if att_type == "text":
                blocks.append(TextBlock(text=str(attachment.get("text", ""))))
                continue
            if att_type == "agent_mention":
                agent_type = str(attachment.get("agent_type", "") or "").strip()
                prompt = str(attachment.get("prompt", "") or "").strip()
                text = f"[Agent mention: {agent_type}]"
                if prompt:
                    text = f"{text}\n{prompt}"
                blocks.append(TextBlock(text=text))
                continue
            if att_type == "ide_selection":
                path = str(attachment.get("path", "") or "").strip()
                selection = str(
                    attachment.get("selection")
                    or attachment.get("text")
                    or attachment.get("content")
                    or ""
                ).strip()
                header = f"[IDE selection: {path}]" if path else "[IDE selection]"
                text = f"{header}\n{selection}" if selection else header
                blocks.append(TextBlock(text=text))
                continue
            if att_type == "queued_command":
                prompt = str(
                    attachment.get("prompt")
                    or attachment.get("text")
                    or attachment.get("content")
                    or ""
                ).strip()
                if prompt:
                    blocks.append(TextBlock(text=prompt))
                continue
            if att_type == "image":
                source = attachment.get("source")
                if isinstance(source, dict) and source.get("type") == "base64":
                    blocks.append(
                        ImageBlock(
                            source=str(source.get("data", "")),
                            media_type=str(source.get("media_type", "image/png")),
                        )
                    )
                elif attachment.get("path"):
                    deferred.append({
                        "type": "image",
                        "path": str(attachment.get("path", "")),
                    })
                continue
            if att_type == "document":
                source = attachment.get("source")
                if isinstance(source, dict) and source.get("type") == "base64":
                    blocks.append(
                        DocumentBlock(
                            source=str(source.get("data", "")),
                            media_type=str(source.get("media_type", "application/pdf")),
                        )
                    )
                elif attachment.get("path"):
                    deferred.append({
                        "type": "file",
                        "path": str(attachment.get("path", "")),
                    })
                continue
            if att_type in {"file", "url", "audio"}:
                deferred.append(dict(attachment))
                continue
            blocks.append(TextBlock(text=str(attachment)))

        return blocks, deferred

    def _fetch_context_parts(
        self,
        *,
        conversation_id: str,
    ) -> tuple[dict[str, str], dict[str, str]]:
        user_context = self._get_user_context(conversation_id)
        user_context.update(
            get_coordinator_user_context(
                self._get_mcp_clients(),
                self._get_scratchpad_dir(),
                active=self._is_coordinator_mode_active(),
                coordinator_tools=[tool.name for tool in self._agent._tools],
                worker_tools=self._get_worker_tool_names(),
            )
        )
        explicit_user_context = self._get_explicit_user_context()
        if explicit_user_context:
            user_context.update(explicit_user_context)

        system_context = {}
        if self._get_custom_system_prompt() is None:
            system_context.update(self._get_system_context())
        explicit_system_context = self._get_explicit_system_context()
        if explicit_system_context:
            system_context.update(explicit_system_context)
        return user_context, system_context

    def _compose_system_prompt(
        self,
        *,
        conversation_id: str,
    ) -> SystemPrompt | str:
        custom_system_prompt = self._get_custom_system_prompt()
        if custom_system_prompt is not None:
            system_prompt = SystemPrompt()
            system_prompt.add_static(custom_system_prompt)
        else:
            base_prompt = getattr(self._agent, "_system_prompt", None)
            if isinstance(base_prompt, SystemPrompt):
                system_prompt = base_prompt.clone()
            elif isinstance(base_prompt, str):
                system_prompt = SystemPrompt()
                if base_prompt.strip():
                    system_prompt.add_static(base_prompt)
            else:
                system_prompt = SystemPrompt()

        memory_mechanics_prompt = self._get_memory_mechanics_prompt(
            conversation_id=conversation_id,
            query=self._agent._current_turn_user_text,
            custom_system_prompt=custom_system_prompt,
        )
        if memory_mechanics_prompt:
            system_prompt.add_static(memory_mechanics_prompt)

        append_system_prompt = self._get_append_system_prompt()
        if append_system_prompt and append_system_prompt.strip():
            system_prompt.add_static(append_system_prompt)

        return system_prompt

    def _get_user_context(self, conversation_id: str) -> dict[str, str]:
        from datetime import date

        from ..prompt_defaults import build_claude_md_context

        context: dict[str, str] = {}

        claude_md = build_claude_md_context()
        if claude_md:
            context["claudeMd"] = claude_md

        adapter = getattr(self._agent, "_memory_adapter", None)
        if adapter is not None:
            try:
                user_profile = adapter.build_user_context(conversation_id)
            except Exception:
                user_profile = None
            if user_profile:
                normalized = str(user_profile).strip()
                if normalized.startswith("# User Profile"):
                    normalized = normalized[len("# User Profile"):].lstrip()
                if normalized:
                    context["userProfile"] = normalized

        current_date = getattr(self._agent, "_current_date", None)
        if current_date:
            context["currentDate"] = f"Today's date is {current_date}."
        else:
            context["currentDate"] = f"Today's date is {date.today().isoformat()}."

        return context

    def _get_system_context(self) -> dict[str, str]:
        from ..prompt_defaults import _build_git_status_snapshot

        cwd = self._get_working_directory()
        git_status = _build_git_status_snapshot(cwd)
        if not git_status:
            return {}
        return {"gitStatus": git_status}

    def _get_explicit_user_context(self) -> dict[str, str]:
        value = getattr(self._agent, "_user_context", None)
        if not isinstance(value, dict):
            return {}
        return {
            str(key): str(item)
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }

    def _get_explicit_system_context(self) -> dict[str, str]:
        value = getattr(self._agent, "_system_context", None)
        if not isinstance(value, dict):
            return {}
        return {
            str(key): str(item)
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }

    def _get_worker_tool_names(self) -> list[str]:
        try:
            from ..delegation.multi_agent import AgentTool

            for tool in getattr(self._agent, "_tools", []):
                if isinstance(tool, AgentTool):
                    return [child.name for child in getattr(tool, "_parent_tools", [])]
        except Exception:
            return []
        return []

    def _is_coordinator_mode_active(self) -> bool:
        coordinator = getattr(self._agent, "_coordinator_mode", None)
        return bool(getattr(coordinator, "is_active", False))

    def _get_memory_mechanics_prompt(
        self,
        *,
        conversation_id: str,
        query: str,
        custom_system_prompt: str | None,
    ) -> str | None:
        has_memory_dir_override = any(
            os.environ.get(env_name, "").strip()
            for env_name in ("MEMORY_DIR", "SESSION_MEMORY_DIR")
        )
        if custom_system_prompt is None or not has_memory_dir_override:
            return None
        adapter = getattr(self._agent, "_memory_adapter", None)
        if adapter is None:
            return None
        try:
            return adapter.build_memory_section(conversation_id, query)
        except Exception:
            return None

    def _get_custom_system_prompt(self) -> str | None:
        value = getattr(self._agent, "_custom_system_prompt", None)
        if value is not None:
            text = str(value).strip()
            return text or None
        return None

    def _get_append_system_prompt(self) -> str | None:
        value = getattr(self._agent, "_append_system_prompt", None)
        if value is not None:
            text = str(value).strip()
            return text or None
        return None

    def _get_mcp_clients(self) -> list[Any]:
        clients = getattr(self._agent, "_mcp_clients", None)
        if isinstance(clients, list):
            return list(clients)
        return []

    def _get_mcp_instructions(self) -> str | None:
        value = getattr(self._agent, "_mcp_instructions", None)
        if value is not None:
            text = str(value).strip()
            if text:
                return text

        manager = getattr(self._agent, "_mcp_manager", None)
        get_instructions = getattr(manager, "get_instructions", None)
        if callable(get_instructions):
            try:
                text = str(get_instructions() or "").strip()
            except Exception:
                return None
            return text or None
        return None

    def _get_scratchpad_dir(self) -> str | None:
        value = getattr(self._agent, "_scratchpad_dir", None)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _get_working_directory(self) -> str:
        value = getattr(self._agent, "_working_directory", None)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
        return self._cwd()

    @staticmethod
    def _cwd() -> str:
        return os.getcwd()

    def _get_session_memory_content(self) -> str | None:
        try:
            from ..services.session_memory import get_session_memory_content

            return get_session_memory_content(self._agent.conversation_id)
        except Exception:
            return None
