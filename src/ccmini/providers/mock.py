"""Mock provider for testing — returns deterministic responses without LLM calls."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..messages import (
    CompletionEvent,
    ContentBlock,
    Message,
    StreamEvent,
    TextBlock,
    TextEvent,
    ToolUseBlock,
    assistant_message,
)
from ..tool import Tool
from . import BaseProvider, ProviderConfig


class MockProvider(BaseProvider):
    """Deterministic mock that echoes user input or runs tools once."""

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self._config = config or ProviderConfig(type="mock", model="mock")
        self._reply_override: str | None = None

    @property
    def model_name(self) -> str:
        return self._config.model or "mock"

    def set_reply(self, text: str) -> None:
        """Set a fixed reply for the next call."""
        self._reply_override = text

    async def stream(
        self,
        *,
        messages: list[Message],
        system: str | list[dict[str, Any]] = "",
        tools: list[Tool] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        task_budget: dict[str, int] | None = None,
        query_source: str = "",
        stop_sequences: list[str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        _ = query_source, stop_sequences  # BaseProvider parity; mock ignores
        reply = self._build_reply(messages)
        self._reply_override = None
        yield TextEvent(text=reply)
        yield CompletionEvent(text=reply, stop_reason="end_turn")

    async def complete(
        self,
        *,
        messages: list[Message],
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        query_source: str = "",
        stop_sequences: list[str] | None = None,
        tools: list[Tool] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Message:
        _ = query_source, stop_sequences, tools, tool_choice
        reply = self._build_reply(messages)
        self._reply_override = None
        return assistant_message(reply)

    def _build_reply(self, messages: list[Message]) -> str:
        if self._reply_override is not None:
            return self._reply_override
        last = messages[-1] if messages else None
        if last is not None:
            user_text = last.text.strip()
            if user_text:
                return f"[mock] received: {user_text}"
        return "[mock] no input"
