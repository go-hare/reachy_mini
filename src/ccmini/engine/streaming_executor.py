"""Direct structural port of Claude Code's ``StreamingToolExecutor.ts``."""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from typing import Any

from .result_budget import BudgetConfig, truncate_tool_result
from ..messages import ToolCallEvent, ToolProgressEvent, ToolResultBlock, ToolResultEvent
from ..tool import ClientTool, Tool, ToolProgress, ToolUseContext, find_tool_by_name

DEFAULT_RESULT_BUDGET = BudgetConfig()
_BASH_TOOL_NAME = "Bash"
# TS does NOT have a hard concurrency cap — concurrency is controlled
# solely by isConcurrencySafe mutual exclusion.  We keep the constant
# for reference but do NOT enforce it in _can_execute_tool.
MAX_TOOL_USE_CONCURRENCY = 10


@dataclass(slots=True)
class MessageUpdate:
    message: ToolProgressEvent | ToolResultEvent | None = None
    new_context: ToolUseContext | None = None


@dataclass(slots=True)
class _TrackedTool:
    id: str
    call: ToolCallEvent
    status: str
    is_concurrency_safe: bool
    tool: Tool | None = None
    promise: asyncio.Task[None] | None = None
    results: list[ToolResultEvent] = field(default_factory=list)
    pending_progress: list[ToolProgressEvent] = field(default_factory=list)


class StreamingToolExecutor:
    """Execute tools as they stream in, matching the reference queue model."""

    def __init__(
        self,
        tool_definitions: dict[str, Tool],
        tool_use_context: ToolUseContext,
        budget: BudgetConfig | None = None,
    ) -> None:
        self._tool_definitions = tool_definitions
        self._tool_use_context = tool_use_context
        self._budget = budget or DEFAULT_RESULT_BUDGET
        self._tools: list[_TrackedTool] = []
        self._has_errored = False
        self._errored_tool_description = ""
        self._discarded = False
        self._progress_available_event = asyncio.Event()

    def discard(self) -> None:
        """Discard pending results from the current attempt."""
        self._discarded = True
        for tool in self._tools:
            if tool.promise is not None and not tool.promise.done():
                tool.promise.cancel()
        self._progress_available_event.set()

    def add_tool(self, call: ToolCallEvent) -> None:
        tool_definition = find_tool_by_name(self._tool_definitions.values(), call.tool_name)
        if tool_definition is None:
            self._tools.append(
                _TrackedTool(
                    id=call.tool_use_id,
                    call=call,
                    status="completed",
                    is_concurrency_safe=True,
                    tool=None,
                    results=[
                        ToolResultEvent(
                            tool_use_id=call.tool_use_id,
                            tool_name=call.tool_name,
                            result=f"<tool_use_error>Error: No such tool available: {call.tool_name}</tool_use_error>",
                            is_error=True,
                        )
                    ],
                )
            )
            return

        try:
            is_concurrency_safe = bool(tool_definition.is_concurrency_safe(call.tool_input))
        except Exception:
            is_concurrency_safe = False

        self._tools.append(
            _TrackedTool(
                id=call.tool_use_id,
                call=call,
                status="queued",
                is_concurrency_safe=is_concurrency_safe,
                tool=tool_definition,
            )
        )
        asyncio.create_task(self._process_queue())

    def _tool_interrupt_behavior(self, tracked: _TrackedTool) -> str:
        tool = tracked.tool
        if tool is None:
            return "block"
        try:
            return tool.interrupt_behavior()
        except Exception:
            return "block"

    def _update_interruptible_state(self) -> None:
        callback = self._tool_use_context.set_has_interruptible_tool_in_progress
        if callback is None:
            return
        executing = [tool for tool in self._tools if tool.status == "executing"]
        callback(bool(executing) and all(
            self._tool_interrupt_behavior(tool) == "cancel"
            for tool in executing
        ))

    def _set_in_progress(self, tool_use_id: str, *, active: bool) -> None:
        callback = self._tool_use_context.set_in_progress_tool_use_ids
        if callback is None:
            return
        current = set(self._tool_use_context.extras.get("in_progress_tool_use_ids", set()))
        if active:
            current.add(tool_use_id)
        else:
            current.discard(tool_use_id)
        self._tool_use_context.extras["in_progress_tool_use_ids"] = current
        callback(current)

    def _tool_description(self, tracked: _TrackedTool) -> str:
        summary = ""
        for key in ("command", "file_path", "pattern"):
            value = tracked.call.tool_input.get(key)
            if isinstance(value, str) and value:
                summary = value
                break
        if summary:
            truncated = f"{summary[:40]}..." if len(summary) > 40 else summary
            return f"{tracked.call.tool_name}({truncated})"
        return tracked.call.tool_name

    def _create_synthetic_result(
        self,
        tracked: _TrackedTool,
        *,
        reason: str,
    ) -> ToolResultEvent:
        if reason == "streaming_fallback":
            message = "Streaming fallback - tool execution discarded"
        elif reason == "user_interrupted":
            message = "User rejected tool use"
        else:
            if self._errored_tool_description:
                message = f"Cancelled: parallel tool call {self._errored_tool_description} errored"
            else:
                message = "Cancelled: parallel tool call errored"
        return ToolResultEvent(
            tool_use_id=tracked.call.tool_use_id,
            tool_name=tracked.call.tool_name,
            result=message,
            is_error=True,
        )

    def _abort_reason(self, tracked: _TrackedTool) -> str | None:
        if self._discarded:
            return "streaming_fallback"
        if self._has_errored:
            return "sibling_error"
        abort_event = self._tool_use_context.abort_event
        if abort_event is not None and abort_event.is_set():
            if self._tool_interrupt_behavior(tracked) == "cancel":
                return "user_interrupted"
        return None

    def _cancel_siblings(self, errored_tool: _TrackedTool) -> None:
        self._has_errored = True
        self._errored_tool_description = self._tool_description(errored_tool)
        for tracked in self._tools:
            if tracked is errored_tool:
                continue
            if tracked.promise is not None and not tracked.promise.done():
                tracked.promise.cancel()
        self._progress_available_event.set()

    def _can_execute_tool(self, is_concurrency_safe: bool) -> bool:
        executing = [tool for tool in self._tools if tool.status == "executing"]
        return not executing or (
            is_concurrency_safe and all(tool.is_concurrency_safe for tool in executing)
        )

    async def _process_queue(self) -> None:
        for tool in self._tools:
            if tool.status != "queued":
                continue
            if self._can_execute_tool(tool.is_concurrency_safe):
                await self._execute_tool(tool)
            elif not tool.is_concurrency_safe:
                break

    async def _execute_tool(self, tracked: _TrackedTool) -> None:
        tracked.status = "executing"
        self._set_in_progress(tracked.call.tool_use_id, active=True)
        self._update_interruptible_state()

        async def _collect_results() -> None:
            initial_abort_reason = self._abort_reason(tracked)
            if initial_abort_reason is not None:
                tracked.results = [
                    self._create_synthetic_result(
                        tracked,
                        reason=initial_abort_reason,
                    )
                ]
                tracked.status = "completed"
                self._set_in_progress(tracked.call.tool_use_id, active=False)
                self._update_interruptible_state()
                self._progress_available_event.set()
                return

            tool = tracked.tool or find_tool_by_name(
                self._tool_definitions.values(),
                tracked.call.tool_name,
            )
            if tool is None:
                tracked.results = [
                    ToolResultEvent(
                        tool_use_id=tracked.call.tool_use_id,
                        tool_name=tracked.call.tool_name,
                        result=f"Error: No such tool available: {tracked.call.tool_name}",
                        is_error=True,
                    )
                ]
                tracked.status = "completed"
                self._set_in_progress(tracked.call.tool_use_id, active=False)
                self._update_interruptible_state()
                self._progress_available_event.set()
                return

            if isinstance(tool, ClientTool):
                tracked.results = [
                    ToolResultEvent(
                        tool_use_id=tracked.call.tool_use_id,
                        tool_name=tracked.call.tool_name,
                        result="[awaiting client execution]",
                        is_error=False,
                    )
                ]
                tracked.status = "completed"
                self._set_in_progress(tracked.call.tool_use_id, active=False)
                self._update_interruptible_state()
                self._progress_available_event.set()
                return

            final_result = ""
            this_tool_errored = False
            try:
                if tool.supports_streaming:
                    async for item in tool.stream_execute(
                        context=self._tool_use_context,
                        **tracked.call.tool_input,
                    ):
                        abort_reason = self._abort_reason(tracked)
                        if abort_reason is not None and not this_tool_errored:
                            tracked.results = [
                                self._create_synthetic_result(
                                    tracked,
                                    reason=abort_reason,
                                )
                            ]
                            tracked.status = "completed"
                            self._set_in_progress(tracked.call.tool_use_id, active=False)
                            self._update_interruptible_state()
                            self._progress_available_event.set()
                            return
                        if isinstance(item, ToolProgress):
                            tracked.pending_progress.append(
                                ToolProgressEvent(
                                    tool_use_id=tracked.call.tool_use_id,
                                    tool_name=tracked.call.tool_name,
                                    content=item.content,
                                    metadata=item.metadata,
                                )
                            )
                            self._progress_available_event.set()
                        elif isinstance(item, str):
                            final_result = item
                else:
                    raw_result = await tool.execute(
                        context=self._tool_use_context,
                        **tracked.call.tool_input,
                    )
                    # Handle ToolResult or plain str
                    from ..tool import ToolResult as _ToolResult
                    if isinstance(raw_result, _ToolResult):
                        final_result = raw_result.output if isinstance(raw_result.output, str) else str(raw_result.output)
                        this_tool_errored = raw_result.behavior == "error"
                        # Apply context modifier if present
                        if raw_result.context_modifier is not None:
                            self._tool_use_context = raw_result.context_modifier(self._tool_use_context)
                    else:
                        final_result = raw_result

                tracked.results = [
                    ToolResultEvent(
                        tool_use_id=tracked.call.tool_use_id,
                        tool_name=tracked.call.tool_name,
                        result=truncate_tool_result(final_result, self._budget),
                        is_error=False,
                    )
                ]
            except Exception as exc:
                error_text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                this_tool_errored = True
                tracked.results = [
                    ToolResultEvent(
                        tool_use_id=tracked.call.tool_use_id,
                        tool_name=tracked.call.tool_name,
                        result=f"Error executing {tracked.call.tool_name}: {error_text}",
                        is_error=True,
                    )
                ]
                if tracked.call.tool_name == _BASH_TOOL_NAME:
                    self._cancel_siblings(tracked)
            except asyncio.CancelledError:
                tracked.results = [
                    self._create_synthetic_result(
                        tracked,
                        reason=self._abort_reason(tracked) or "sibling_error",
                    )
                ]
            tracked.status = "completed"
            self._set_in_progress(tracked.call.tool_use_id, active=False)
            self._update_interruptible_state()
            self._progress_available_event.set()

        tracked.promise = asyncio.create_task(_collect_results())
        tracked.promise.add_done_callback(lambda _task: asyncio.create_task(self._process_queue()))

    def get_completed_results(self) -> Generator[MessageUpdate, None, None]:
        if self._discarded:
            return

        for tool in self._tools:
            while tool.pending_progress:
                yield MessageUpdate(
                    message=tool.pending_progress.pop(0),
                    new_context=self._tool_use_context,
                )

            if tool.status == "yielded":
                continue

            if tool.status == "completed" and tool.results:
                tool.status = "yielded"
                for message in tool.results:
                    yield MessageUpdate(message=message, new_context=self._tool_use_context)
                self._set_in_progress(tool.call.tool_use_id, active=False)
            elif tool.status == "executing" and not tool.is_concurrency_safe:
                break

    def _has_pending_progress(self) -> bool:
        return any(tool.pending_progress for tool in self._tools)

    def _has_completed_results(self) -> bool:
        return any(tool.status == "completed" for tool in self._tools)

    def _has_executing_tools(self) -> bool:
        return any(tool.status == "executing" for tool in self._tools)

    def _has_unfinished_tools(self) -> bool:
        return any(tool.status != "yielded" for tool in self._tools)

    async def get_remaining_results(self) -> AsyncGenerator[MessageUpdate, None]:
        if self._discarded:
            return

        while self._has_unfinished_tools():
            await self._process_queue()

            for result in self.get_completed_results():
                yield result

            if (
                self._has_executing_tools()
                and not self._has_completed_results()
                and not self._has_pending_progress()
            ):
                self._progress_available_event.clear()
                executing = [
                    tool.promise
                    for tool in self._tools
                    if tool.status == "executing" and tool.promise is not None
                ]
                if executing:
                    progress_wait = asyncio.create_task(self._progress_available_event.wait())
                    done, pending = await asyncio.wait(
                        [*executing, progress_wait],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        if task is progress_wait:
                            task.cancel()
                    for task in done:
                        if task is progress_wait:
                            continue

        for result in self.get_completed_results():
            yield result

    def get_result_blocks(self) -> list[ToolResultBlock]:
        blocks: list[ToolResultBlock] = []
        for tool in self._tools:
            for result in tool.results:
                blocks.append(
                    ToolResultBlock(
                        tool_use_id=result.tool_use_id,
                        content=result.result,
                        is_error=result.is_error,
                    )
                )
        return blocks

    def get_updated_context(self) -> ToolUseContext:
        return self._tool_use_context


__all__ = [
    "MessageUpdate",
    "StreamingToolExecutor",
]
