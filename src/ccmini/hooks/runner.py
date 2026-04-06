"""Minimal hook dispatcher aligned to the reference hook helpers."""

from __future__ import annotations

import fnmatch
import logging
from typing import TYPE_CHECKING, Any

from . import (
    Hook,
    NotificationHook,
    OnStreamEventHook,
    PostQueryHook,
    PostSamplingContext,
    PostSamplingHook,
    PostToolUseHook,
    PreQueryHook,
    PreToolUseHook,
    PreToolUseResult,
    SessionEndHook,
    SessionStartHook,
    StopHook,
    ToolUseDecision,
)
from ..messages import Message, StreamEvent, ToolCallEvent, ToolResultEvent

if TYPE_CHECKING:
    from ..agent import Agent

logger = logging.getLogger(__name__)


class HookRunner:
    """Classify hooks once and dispatch them without local policy layers."""

    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self.pre_query: list[PreQueryHook] = []
        self.post_query: list[PostQueryHook] = []
        self.stream_event: list[OnStreamEventHook] = []
        self.pre_tool_use: list[PreToolUseHook] = []
        self.post_tool_use: list[PostToolUseHook] = []
        self.session_start: list[SessionStartHook] = []
        self.session_end: list[SessionEndHook] = []
        self.stop: list[StopHook] = []
        self.notification: list[NotificationHook] = []
        self.post_sampling: list[PostSamplingHook] = []

        for hook in hooks or []:
            self.add(hook)

    def add(self, hook: Hook) -> None:
        if isinstance(hook, PreQueryHook):
            self.pre_query.append(hook)
        if isinstance(hook, PostQueryHook):
            self.post_query.append(hook)
        if isinstance(hook, OnStreamEventHook):
            self.stream_event.append(hook)
        if isinstance(hook, PreToolUseHook):
            self.pre_tool_use.append(hook)
        if isinstance(hook, PostToolUseHook):
            self.post_tool_use.append(hook)
        if isinstance(hook, SessionStartHook):
            self.session_start.append(hook)
        if isinstance(hook, SessionEndHook):
            self.session_end.append(hook)
        if isinstance(hook, StopHook):
            self.stop.append(hook)
        if isinstance(hook, NotificationHook):
            self.notification.append(hook)
        if isinstance(hook, PostSamplingHook):
            self.post_sampling.append(hook)

    async def run_pre_query(
        self,
        *,
        user_text: str,
        messages: list[Message],
        agent: Agent,
    ) -> None:
        for hook in self.pre_query:
            try:
                await hook.on_pre_query(user_text=user_text, messages=messages, agent=agent)
            except Exception:
                logger.warning("PreQueryHook error", exc_info=True)

    async def run_post_query(
        self,
        *,
        user_text: str,
        reply: str,
        agent: Agent,
    ) -> None:
        for hook in self.post_query:
            try:
                await hook.on_post_query(user_text=user_text, reply=reply, agent=agent)
            except Exception:
                logger.warning("PostQueryHook error", exc_info=True)

    async def run_stream_event(
        self,
        event: StreamEvent,
        *,
        agent: Agent | None = None,
    ) -> StreamEvent | None:
        current: StreamEvent | None = event
        for hook in self.stream_event:
            if current is None:
                break
            try:
                current = await hook.on_stream_event(current, agent=agent)  # type: ignore[arg-type]
            except Exception:
                logger.warning("OnStreamEventHook error", exc_info=True)
        return current

    async def run_pre_tool_use(
        self,
        event: ToolCallEvent,
        *,
        agent: Agent,
    ) -> PreToolUseResult:
        for hook in self.pre_tool_use:
            matcher = str(getattr(hook, "matcher", "") or "").strip()
            if matcher and not fnmatch.fnmatch(event.tool_name, matcher):
                continue
            try:
                result = await hook.on_pre_tool_use(event, agent=agent)
            except Exception:
                logger.warning("PreToolUseHook error", exc_info=True)
                continue
            if result.decision != ToolUseDecision.ALLOW:
                return result
        return PreToolUseResult(decision=ToolUseDecision.ALLOW)

    async def run_post_tool_use(
        self,
        call: ToolCallEvent,
        result: ToolResultEvent,
        *,
        agent: Agent,
    ) -> ToolResultEvent:
        current = result
        for hook in self.post_tool_use:
            matcher = str(getattr(hook, "matcher", "") or "").strip()
            if matcher and not fnmatch.fnmatch(call.tool_name, matcher):
                continue
            try:
                modified = await hook.on_post_tool_use(call, current, agent=agent)
            except Exception:
                logger.warning("PostToolUseHook error", exc_info=True)
                continue
            if modified is not None:
                current = modified
        return current

    async def run_session_start(self, *, agent: Agent) -> None:
        for hook in self.session_start:
            try:
                await hook.on_session_start(agent=agent)
            except Exception:
                logger.warning("SessionStartHook error", exc_info=True)

    async def run_session_end(self, *, agent: Agent) -> None:
        for hook in self.session_end:
            try:
                await hook.on_session_end(agent=agent)
            except Exception:
                logger.warning("SessionEndHook error", exc_info=True)

    async def should_stop(
        self,
        *,
        reply_text: str,
        turn: int,
        agent: Agent,
    ) -> "StopHookResult":
        """Run all StopHooks; returns aggregated StopHookResult.

        Mirrors TS ``executeStopHooks`` — collects blockingErrors from all
        hooks and sets preventContinuation if any hook requests it.
        """
        from . import StopHookResult
        blocking_errors: list[str] = []
        prevent = False
        for hook in self.stop:
            try:
                raw = await hook.should_stop(reply_text=reply_text, turn=turn, agent=agent)
                if isinstance(raw, bool):
                    if raw:
                        prevent = True
                elif isinstance(raw, StopHookResult):
                    if raw.prevent_continuation:
                        prevent = True
                    blocking_errors.extend(raw.blocking_errors)
            except Exception:
                logger.warning("StopHook error", exc_info=True)
        return StopHookResult(blocking_errors=blocking_errors, prevent_continuation=prevent)

    async def notify(
        self,
        *,
        level: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        for hook in self.notification:
            try:
                await hook.on_notification(level=level, message=message, data=data)
            except Exception:
                logger.warning("NotificationHook error", exc_info=True)

    async def run_post_sampling(
        self,
        context: PostSamplingContext,
        *,
        agent: Agent,
    ) -> None:
        for hook in self.post_sampling:
            try:
                await hook.on_post_sampling(context, agent=agent)
            except Exception:
                logger.warning("PostSamplingHook error", exc_info=True)
