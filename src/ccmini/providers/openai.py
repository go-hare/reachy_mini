"""OpenAI Chat Completions API provider.

Translates between mini-agent's Anthropic-style content blocks and
OpenAI's message/tool_calls format.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

# https://platform.openai.com/docs/api-reference/chat/create — stop: up to 4 sequences
_OPENAI_MAX_STOPS = 4
_OPENAI_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}

from ..messages import (
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    Message,
    StreamEvent,
    TextBlock,
    TextEvent,
    ToolCallEvent,
    ToolResultBlock,
    ToolUseBlock,
    UsageEvent,
    assistant_message,
)
from .retry import RetryConfig, with_retry, with_retry_stream
from ..tool import Tool
from . import BaseProvider, ProviderConfig


def _openai_stop_sequences(stop_sequences: list[str] | None) -> list[str] | None:
    """Return at most ``_OPENAI_MAX_STOPS`` non-empty stop strings (API limit)."""
    if not stop_sequences:
        return None
    filtered = [s for s in stop_sequences if s]
    if not filtered:
        return None
    if len(filtered) > _OPENAI_MAX_STOPS:
        logger.warning(
            "OpenAI chat.completions accepts at most %d stop sequences; got %d, truncating",
            _OPENAI_MAX_STOPS,
            len(filtered),
        )
        return filtered[:_OPENAI_MAX_STOPS]
    return filtered


def _canonical_model_name(model: str) -> str:
    return model.strip().lower().replace("_", "-")


def _supports_openai_reasoning_effort(model: str) -> bool:
    canonical = _canonical_model_name(model)
    return (
        canonical.startswith("gpt-5")
        or canonical.startswith("o1")
        or canonical.startswith("o3")
        or canonical.startswith("o4")
    )


def _resolve_openai_reasoning_effort(config: ProviderConfig) -> str | None:
    extras = config.extras if isinstance(config.extras, dict) else {}
    raw_value = extras.get("reasoning_effort", os.getenv("CCMINI_REASONING_EFFORT", "medium"))
    value = str(raw_value).strip().lower()
    if value not in _OPENAI_REASONING_EFFORTS:
        return None
    return value


class OpenAIProvider(BaseProvider):
    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._retry_config: RetryConfig = config.retry
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import openai
            except ImportError as exc:
                raise ImportError(
                    "Install the openai SDK: pip install openai"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self._config.api_key:
                kwargs["api_key"] = self._config.api_key
            if self._config.base_url:
                kwargs["base_url"] = self._config.base_url
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    @property
    def model_name(self) -> str:
        return self._config.model

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

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
        client = self._ensure_client()
        api_messages = _build_openai_messages(messages, system)
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": api_messages,
            "max_tokens": max_tokens or self._config.max_tokens,
            "stream": True,
            # Request usage stats in the final streamed chunk.
            # Without this, chunk.usage is always None.
            "stream_options": {"include_usage": True},
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        elif self._config.temperature is not None:
            kwargs["temperature"] = self._config.temperature
        if tools:
            kwargs["tools"] = [_tool_to_openai(t) for t in tools]
        stops = _openai_stop_sequences(stop_sequences)
        if stops:
            kwargs["stop"] = stops
        reasoning_effort = _resolve_openai_reasoning_effort(self._config)
        if (
            reasoning_effort is not None
            and _supports_openai_reasoning_effort(self._config.model)
        ):
            kwargs["reasoning_effort"] = reasoning_effort

        pending_tool_calls: dict[int, dict[str, Any]] = {}
        usage_acc: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        last_finish_reason: str | None = None

        async def _create() -> Any:
            return await client.chat.completions.create(**kwargs)

        response = await with_retry_stream(
            _create,
            self._retry_config,
            query_source=query_source or "main",
            model=self._config.model,
        )
        async for chunk in response:
            # Accumulate usage from any chunk that carries it.
            # With stream_options.include_usage the FINAL chunk carries
            # usage and has choices=[].  The TS updateUsage semantics:
            #   - input_tokens: replacement with > 0 guard
            #   - output_tokens: null-coalesce (0 *does* overwrite)
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                prompt = getattr(chunk_usage, "prompt_tokens", 0) or 0
                if prompt > 0:
                    usage_acc["input_tokens"] = prompt
                completion = getattr(chunk_usage, "completion_tokens", None)
                if completion is not None:
                    usage_acc["output_tokens"] = completion

            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue
            delta = choice.delta

            if delta.content:
                yield TextEvent(text=delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in pending_tool_calls:
                        pending_tool_calls[idx] = {
                            "id": tc.id or "",
                            "name": tc.function.name or "" if tc.function else "",
                            "arguments": "",
                        }
                    entry = pending_tool_calls[idx]
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["name"] = tc.function.name
                        if tc.function.arguments:
                            entry["arguments"] += tc.function.arguments

            if choice.finish_reason is not None:
                last_finish_reason = choice.finish_reason
                # Flush any pending tool calls regardless of finish_reason.
                # Some models return finish_reason="stop" even when tool
                # calls are present; the TS source emits tool calls at
                # content_block_stop unconditionally.
                if pending_tool_calls:
                    for entry in pending_tool_calls.values():
                        args = _safe_parse_json(entry["arguments"])
                        yield ToolCallEvent(
                            tool_use_id=entry["id"],
                            tool_name=entry["name"],
                            tool_input=args,
                        )
                    pending_tool_calls.clear()

        # Emit UsageEvent AFTER the stream loop ends.  OpenAI sends the
        # final usage chunk (with stream_options.include_usage) AFTER the
        # chunk that carries finish_reason, and that final chunk has
        # choices=[].  Emitting here ensures usage_acc reflects the real
        # token counts from the final chunk.
        yield UsageEvent(
            input_tokens=usage_acc["input_tokens"],
            output_tokens=usage_acc["output_tokens"],
            model=self._config.model,
            stop_reason=last_finish_reason,
        )

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

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
        client = self._ensure_client()
        api_messages = _build_openai_messages(messages, system)
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": api_messages,
            "max_tokens": max_tokens or self._config.max_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        stops = _openai_stop_sequences(stop_sequences)
        if stops:
            kwargs["stop"] = stops
        if tools:
            kwargs["tools"] = [_tool_to_openai(t) for t in tools]
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        reasoning_effort = _resolve_openai_reasoning_effort(self._config)
        if (
            reasoning_effort is not None
            and _supports_openai_reasoning_effort(self._config.model)
        ):
            kwargs["reasoning_effort"] = reasoning_effort

        async def _call() -> Any:
            return await client.chat.completions.create(**kwargs)

        response = await with_retry(
            _call,
            self._retry_config,
            query_source=query_source or "main",
            model=self._config.model,
        )
        return _from_openai_response(response)


# ======================================================================
# Format conversion
# ======================================================================

def _build_openai_messages(
    messages: list[Message],
    system: str | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert internal messages to OpenAI Chat Completions format."""
    result: list[dict[str, Any]] = []

    sys_text = system if isinstance(system, str) else json.dumps(system, ensure_ascii=False)
    if sys_text.strip():
        result.append({"role": "system", "content": sys_text})

    for msg in messages:
        if msg.role == "system":
            continue
        if isinstance(msg.content, str):
            result.append({"role": msg.role, "content": msg.content})
            continue

        tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        if tool_results:
            for tr in tool_results:
                content = tr.content
                # Prefix error indicator so the model knows the call failed,
                # matching the TS source's is_error handling.
                if tr.is_error:
                    content = f"Error: {content}" if content else "Error"
                result.append({
                    "role": "tool",
                    "tool_call_id": tr.tool_use_id,
                    "content": content,
                })
            continue

        image_blocks = [b for b in msg.content if isinstance(b, ImageBlock)]
        if image_blocks and msg.role == "user":
            parts: list[dict[str, Any]] = []
            for b in msg.content:
                if isinstance(b, TextBlock):
                    parts.append({"type": "text", "text": b.text})
                elif isinstance(b, ImageBlock):
                    parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{b.media_type};base64,{b.source}",
                        },
                    })
            result.append({"role": "user", "content": parts})
            continue

        document_blocks = [b for b in msg.content if isinstance(b, DocumentBlock)]
        if document_blocks and msg.role == "user":
            text_parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
            text_parts.extend("[document attached]" for _ in document_blocks)
            result.append({"role": "user", "content": "\n".join(text_parts)})
            continue

        tool_uses = [b for b in msg.content if isinstance(b, ToolUseBlock)]
        text_parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
        text_content = "\n".join(text_parts) if text_parts else None

        if tool_uses and msg.role == "assistant":
            api_msg: dict[str, Any] = {"role": "assistant"}
            # OpenAI requires 'content' to be present (can be null)
            # when tool_calls is set.
            api_msg["content"] = text_content
            api_msg["tool_calls"] = [
                {
                    "id": tu.id,
                    "type": "function",
                    "function": {
                        "name": tu.name,
                        "arguments": json.dumps(tu.input, ensure_ascii=False),
                    },
                }
                for tu in tool_uses
            ]
            result.append(api_msg)
        elif text_content is not None:
            result.append({"role": msg.role, "content": text_content})

    return result


def _tool_to_openai(tool: Tool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.get_parameters_schema(),
        },
    }


def _from_openai_response(response: Any) -> Message:
    choice = response.choices[0]
    msg = choice.message
    blocks: list[ContentBlock] = []

    if msg.content:
        blocks.append(TextBlock(text=msg.content))

    if msg.tool_calls:
        for tc in msg.tool_calls:
            args = _safe_parse_json(tc.function.arguments)
            blocks.append(ToolUseBlock(
                id=tc.id,
                name=tc.function.name,
                input=args,
            ))

    return assistant_message(blocks if blocks else (msg.content or ""))


def _safe_parse_json(text: str) -> dict[str, Any]:
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
