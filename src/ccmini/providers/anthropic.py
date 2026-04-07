"""Anthropic Messages API provider.

Maps mini-agent internal types to Anthropic's native format, which is
the closest match (content blocks, tool_use/tool_result, streaming).
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

from ..messages import (
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    Message,
    ThinkingEvent,
    StreamEvent,
    TextBlock,
    TextEvent,
    ToolCallEvent,
    ToolResultBlock,
    ToolUseBlock,
    UsageEvent,
    assistant_message,
    normalize_tool_result_content,
)
from .retry import RetryConfig, with_retry
from ..tool import Tool
from . import BaseProvider, ProviderConfig

_DEFAULT_ANTHROPIC_THINKING_BUDGET = 2048


class AnthropicProvider(BaseProvider):
    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._retry_config: RetryConfig = config.retry
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ImportError(
                    "Install the anthropic SDK: pip install anthropic"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self._config.api_key:
                kwargs["api_key"] = self._config.api_key
            if self._config.base_url:
                kwargs["base_url"] = self._config.base_url
            self._client = anthropic.AsyncAnthropic(**kwargs)
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
        use_cache = self._config.enable_cache
        api_messages = [_to_api_message(m) for m in messages if m.role != "system"]
        if use_cache:
            _inject_conversation_cache_breakpoints(api_messages)
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": api_messages,
            "max_tokens": max_tokens or self._config.max_tokens,
            "stream": True,
        }
        total_budget = task_budget.get("total") if task_budget else None
        if total_budget is not None:
            kwargs["betas"] = ["task-budgets-2026-03-13"]
            kwargs["output_config"] = {
                "task_budget": {
                    "type": "tokens",
                    "total": total_budget,
                    **(
                        {"remaining": task_budget["remaining"]}
                        if task_budget is not None and "remaining" in task_budget
                        else {}
                    ),
                }
            }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        elif self._config.temperature is not None:
            kwargs["temperature"] = self._config.temperature
        if tools:
            kwargs["tools"] = [_tool_to_api(t) for t in tools]
        if stop_sequences:
            kwargs["stop_sequences"] = stop_sequences
        thinking = _resolve_anthropic_thinking(
            self._config,
            int(kwargs["max_tokens"]),
        )
        if thinking is not None:
            kwargs["thinking"] = thinking

        # Use raw stream (messages.create with stream=True) instead of the
        # high-level messages.stream() helper.  The TS source explicitly avoids
        # the high-level wrapper because it calls partialParse() on every
        # input_json_delta, causing O(n²) partial JSON parsing overhead.
        async def _open_stream() -> Any:
            return await client.messages.create(**kwargs)

        raw_stream = await with_retry(
            _open_stream,
            self._retry_config,
            query_source=query_source or "main",
            model=self._config.model,
        )

        pending_tools: dict[int, dict[str, Any]] = {}
        pending_thinking_blocks: dict[int, bool] = {}
        # TS uses replacement semantics (not accumulation) for usage fields.
        # input_tokens / cache tokens arrive in message_start, output_tokens
        # in message_delta — each overwrites the previous value when > 0.
        usage_acc: dict[str, Any] = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "stop_reason": "",
        }
        async for event in raw_stream:
            for mapped in _map_stream_event(
                event,
                pending_tools,
                pending_thinking_blocks,
                usage_acc,
                self._config.model,
            ):
                yield mapped

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

    async def complete(
        self,
        *,
        messages: list[Message],
        system: str | list[dict[str, Any]] = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        query_source: str = "",
        stop_sequences: list[str] | None = None,
        tools: list[Tool] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Message:
        client = self._ensure_client()
        use_cache = self._config.enable_cache
        api_messages = [_to_api_message(m) for m in messages if m.role != "system"]
        if use_cache:
            _inject_conversation_cache_breakpoints(api_messages)
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": api_messages,
            "max_tokens": max_tokens or self._config.max_tokens,
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        if stop_sequences:
            kwargs["stop_sequences"] = stop_sequences
        if tools:
            kwargs["tools"] = [_tool_to_api(t) for t in tools]
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        thinking = _resolve_anthropic_thinking(
            self._config,
            int(kwargs["max_tokens"]),
        )
        if thinking is not None:
            kwargs["thinking"] = thinking

        async def _call() -> Any:
            return await client.messages.create(**kwargs)

        response = await with_retry(
            _call,
            self._retry_config,
            query_source=query_source or "main",
            model=self._config.model,
        )
        return _from_api_response(response)


# ======================================================================
# Format conversion helpers
# ======================================================================


def _canonical_model_name(model: str) -> str:
    return model.strip().lower().replace("_", "-").replace(".", "-")


def _coerce_positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _model_supports_anthropic_thinking(model: str) -> bool:
    canonical = _canonical_model_name(model)
    if canonical in {"sonnet", "opus", "haiku", "sonnet-4-6", "opus-4-6", "haiku-4"}:
        return True
    return any(
        marker in canonical
        for marker in (
            "sonnet-4-6",
            "opus-4-6",
            "haiku-4",
            "sonnet-4",
            "opus-4",
            "claude-4",
            "claude-3-7",
        )
    )


def _model_supports_adaptive_thinking(model: str) -> bool:
    canonical = _canonical_model_name(model)
    return canonical in {"sonnet", "opus", "sonnet-4-6", "opus-4-6"} or any(
        marker in canonical for marker in ("sonnet-4-6", "opus-4-6")
    )


def _resolve_anthropic_thinking(
    config: ProviderConfig,
    max_tokens: int,
) -> dict[str, Any] | None:
    if max_tokens <= 1:
        return None

    extras = config.extras if isinstance(config.extras, dict) else {}
    env_mode = os.getenv("CCMINI_THINKING", "").strip().lower()
    env_budget = _coerce_positive_int(os.getenv("CCMINI_MAX_THINKING_TOKENS"))
    explicit = extras.get("thinking")

    if explicit is False or env_mode == "disabled":
        return None

    if isinstance(explicit, dict):
        explicit_type = str(explicit.get("type", "")).strip().lower()
        if explicit_type == "adaptive":
            return {"type": "adaptive"}
        if explicit_type == "enabled":
            budget = _coerce_positive_int(explicit.get("budget_tokens"))
            if budget is None:
                budget = min(_DEFAULT_ANTHROPIC_THINKING_BUDGET, max_tokens - 1)
            return {
                "type": "enabled",
                "budget_tokens": min(max_tokens - 1, budget),
            }
        return None

    if not _model_supports_anthropic_thinking(config.model):
        return None

    mode = str(extras.get("thinking_mode", env_mode or "")).strip().lower()
    budget = _coerce_positive_int(extras.get("thinking_budget_tokens")) or env_budget

    if mode == "adaptive":
        return {"type": "adaptive"}

    if mode == "enabled":
        resolved_budget = budget or min(
            _DEFAULT_ANTHROPIC_THINKING_BUDGET,
            max_tokens - 1,
        )
        return {
            "type": "enabled",
            "budget_tokens": min(max_tokens - 1, resolved_budget),
        }

    if _model_supports_adaptive_thinking(config.model):
        return {"type": "adaptive"}

    resolved_budget = budget or min(
        _DEFAULT_ANTHROPIC_THINKING_BUDGET,
        max_tokens - 1,
    )
    return {
        "type": "enabled",
        "budget_tokens": min(max_tokens - 1, resolved_budget),
    }


def _to_api_message(msg: Message) -> dict[str, Any]:
    """Convert internal Message to Anthropic API format."""
    if isinstance(msg.content, str):
        return {"role": msg.role, "content": msg.content}

    blocks: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            blocks.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
        elif isinstance(block, ToolResultBlock):
            blocks.append({
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": normalize_tool_result_content(block.content),
                **({"is_error": True} if block.is_error else {}),
            })
        elif isinstance(block, ImageBlock):
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.media_type,
                    "data": block.source,
                },
            })
        elif isinstance(block, DocumentBlock):
            blocks.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": block.media_type,
                    "data": block.source,
                },
            })
    return {"role": msg.role, "content": blocks}


def _tool_to_api(tool: Tool) -> dict[str, Any]:
    """Convert a Tool to the Anthropic tools API format."""
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.get_parameters_schema(),
    }


def _from_api_response(response: Any) -> Message:
    """Convert an Anthropic API response to an internal Message."""
    blocks: list[ContentBlock] = []
    for block in response.content:
        if block.type == "text":
            blocks.append(TextBlock(text=block.text))
        elif block.type == "tool_use":
            blocks.append(ToolUseBlock(
                id=block.id,
                name=block.name,
                input=block.input,
            ))
    return assistant_message(blocks)


def _map_stream_event(
    event: Any,
    pending_tools: dict[int, dict[str, Any]],
    pending_thinking_blocks: dict[int, bool],
    usage_acc: dict[str, Any],
    model: str,
) -> list[StreamEvent]:
    """Map a single Anthropic stream event to mini-agent events.

    ``pending_tools`` and ``usage_acc`` are caller-owned mutable state.
    """
    results: list[StreamEvent] = []
    event_type = getattr(event, "type", "")
    if event_type == "content_block_delta":
        delta = event.delta
        delta_type = getattr(delta, "type", "")
        if delta_type == "text_delta":
            results.append(TextEvent(text=delta.text))
        elif delta_type == "thinking_delta":
            results.append(
                ThinkingEvent(
                    text=getattr(delta, "thinking", "") or "",
                    is_redacted=False,
                    phase="delta",
                    source="model",
                )
            )
        elif delta_type == "signature_delta":
            results.append(
                ThinkingEvent(
                    text="",
                    is_redacted=False,
                    phase="delta",
                    source="model",
                    signature=getattr(delta, "signature", "") or "",
                )
            )
        elif delta_type == "input_json_delta":
            idx = getattr(event, "index", 0)
            if idx in pending_tools:
                pending_tools[idx]["input_json"] += delta.partial_json

    elif event_type == "content_block_start":
        block = event.content_block
        idx = getattr(event, "index", 0)
        block_type = getattr(block, "type", "")
        if block_type == "tool_use":
            pending_tools[idx] = {
                "id": block.id,
                "name": block.name,
                "input_json": "",
            }
        elif block_type == "thinking":
            pending_thinking_blocks[idx] = False
            results.append(
                ThinkingEvent(
                    phase="start",
                    source="model",
                )
            )
        elif block_type == "redacted_thinking":
            pending_thinking_blocks[idx] = True
            results.append(
                ThinkingEvent(
                    is_redacted=True,
                    phase="start",
                    source="model",
                )
            )

    elif event_type == "content_block_stop":
        idx = getattr(event, "index", 0)
        pending = pending_tools.pop(idx, None)
        if pending is not None:
            try:
                tool_input = json.loads(pending["input_json"]) if pending["input_json"] else {}
            except json.JSONDecodeError:
                tool_input = {}
            results.append(ToolCallEvent(
                tool_use_id=pending["id"],
                tool_name=pending["name"],
                tool_input=tool_input if isinstance(tool_input, dict) else {},
            ))
        thinking_is_redacted = pending_thinking_blocks.pop(idx, None)
        if thinking_is_redacted is not None:
            results.append(
                ThinkingEvent(
                    is_redacted=thinking_is_redacted,
                    phase="end",
                    source="model",
                )
            )

    elif event_type == "message_start":
        msg = getattr(event, "message", None)
        if msg is not None:
            u = getattr(msg, "usage", None)
            if u is not None:
                # TS updateUsage: replacement semantics — take new value when > 0.
                inp = getattr(u, "input_tokens", 0)
                if inp and inp > 0:
                    usage_acc["input_tokens"] = inp
                cr = getattr(u, "cache_read_input_tokens", 0)
                if cr and cr > 0:
                    usage_acc["cache_read_tokens"] = cr
                cc = getattr(u, "cache_creation_input_tokens", 0)
                if cc and cc > 0:
                    usage_acc["cache_creation_tokens"] = cc

    elif event_type == "message_delta":
        u = getattr(event, "usage", None)
        if u is not None:
            # TS updateUsage: same function handles both message_start and
            # message_delta.  input/cache fields use > 0 guard (message_delta
            # may send explicit 0 that must not overwrite message_start values).
            # output_tokens uses null-coalesce (0 *does* overwrite).
            inp = getattr(u, "input_tokens", 0)
            if inp and inp > 0:
                usage_acc["input_tokens"] = inp
            cr = getattr(u, "cache_read_input_tokens", 0)
            if cr and cr > 0:
                usage_acc["cache_read_tokens"] = cr
            cc = getattr(u, "cache_creation_input_tokens", 0)
            if cc and cc > 0:
                usage_acc["cache_creation_tokens"] = cc
            out = getattr(u, "output_tokens", None)
            if out is not None:
                usage_acc["output_tokens"] = out
        delta = getattr(event, "delta", None)
        stop_reason = getattr(delta, "stop_reason", None)
        if stop_reason:
            usage_acc["stop_reason"] = stop_reason

    elif event_type == "message_stop":
        results.append(UsageEvent(
            input_tokens=usage_acc["input_tokens"],
            output_tokens=usage_acc["output_tokens"],
            cache_read_tokens=usage_acc["cache_read_tokens"],
            cache_creation_tokens=usage_acc["cache_creation_tokens"],
            model=model,
            stop_reason=usage_acc.get("stop_reason") or None,
        ))

    return results


def _inject_conversation_cache_breakpoints(api_messages: list[dict[str, Any]]) -> None:
    """Add ``cache_control`` to the last message in the conversation.

    The TS source places exactly one message-level cache_control marker on the
    last message (``messages.length - 1``), regardless of role.  This ensures
    the entire conversation prefix is cached and only the next appended message
    invalidates the tail.

    For assistant messages the TS source skips ``thinking`` and
    ``redacted_thinking`` blocks when choosing which block gets the marker —
    the cache_control goes on the last *non-thinking* block.
    """
    if not api_messages:
        return
    target_idx = len(api_messages) - 1
    msg = api_messages[target_idx]
    content = msg.get("content")
    if isinstance(content, str):
        msg["content"] = [{
            "type": "text",
            "text": content,
            "cache_control": {"type": "ephemeral"},
        }]
    elif isinstance(content, list) and content:
        # Find the last block that is not thinking/redacted_thinking
        # (TS: assistantMessageToMessageParam skips these types)
        candidate_idx = len(content) - 1
        if msg.get("role") == "assistant":
            for i in range(len(content) - 1, -1, -1):
                block = content[i]
                if isinstance(block, dict) and block.get("type") in (
                    "thinking", "redacted_thinking",
                ):
                    continue
                candidate_idx = i
                break
        last_block = content[candidate_idx]
        if isinstance(last_block, dict):
            last_block["cache_control"] = {"type": "ephemeral"}
