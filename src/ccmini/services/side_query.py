"""Side query — lightweight auxiliary model calls.

A secondary API call path for tasks outside the main conversation
loop: classifiers, memory extraction, structured outputs, summaries.

Mirrors Claude Code's ``sideQuery`` utility:
- Does NOT participate in the main conversation state
- Uses the same provider or a lightweight one
- Supports forced tool_choice, structured output, temperature override
- Capped retries (default 2)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..messages import Message, TextBlock, ToolUseBlock, user_message
from ..providers import BaseProvider
from ..tool import Tool

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SideQueryOptions:
    """Options for a side query call."""

    system: str = ""
    messages: list[Message] = field(default_factory=list)
    max_tokens: int = 1024
    max_retries: int = 2
    temperature: float | None = None
    stop_sequences: list[str] | None = None
    query_source: str = "side_query"
    tools: list[Tool] | None = None
    tool_choice: dict[str, Any] | None = None


@dataclass(slots=True)
class SideQueryResult:
    """Result of a side query."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    model: str = ""


async def side_query(
    provider: BaseProvider,
    opts: SideQueryOptions,
) -> SideQueryResult:
    """Execute a lightweight model call outside the main conversation.

    This is the foundation for many subsystems:
    - Permission classifiers (auto-mode)
    - Session memory extraction
    - Compact summarisation
    - Tool-use summaries
    - Prompt suggestions
    """
    start = time.monotonic()
    last_exc: BaseException | None = None

    for attempt in range(opts.max_retries + 1):
        try:
            response = await provider.complete(
                messages=opts.messages,
                system=opts.system,
                max_tokens=opts.max_tokens,
                temperature=opts.temperature,
                query_source=opts.query_source,
                stop_sequences=opts.stop_sequences,
                tools=opts.tools,
                tool_choice=opts.tool_choice,
            )

            elapsed_ms = int((time.monotonic() - start) * 1000)

            text = ""
            tool_calls: list[dict[str, Any]] = []
            if isinstance(response.content, str):
                text = response.content
            elif isinstance(response.content, list):
                for block in response.content:
                    if isinstance(block, TextBlock):
                        text += block.text
                    elif isinstance(block, ToolUseBlock):
                        tool_calls.append({
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })

            usage = response.metadata.get("usage", {})
            return SideQueryResult(
                text=text.strip(),
                tool_calls=tool_calls,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                duration_ms=elapsed_ms,
                model=provider.model_name,
            )

        except Exception as exc:
            last_exc = exc
            if attempt < opts.max_retries:
                delay = 0.5 * (2 ** attempt)
                logger.warning(
                    "Side query retry %d/%d after %.1fs: %s",
                    attempt + 1, opts.max_retries, delay, exc,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("Side query failed after %d attempts: %s", opts.max_retries + 1, exc)
                raise

    assert last_exc is not None
    raise last_exc


async def side_query_text(
    provider: BaseProvider,
    *,
    prompt: str,
    system: str = "",
    max_tokens: int = 1024,
    temperature: float | None = 0.0,
) -> str:
    """Convenience: single-prompt side query returning just the text."""
    result = await side_query(
        provider,
        SideQueryOptions(
            system=system,
            messages=[user_message(prompt)],
            max_tokens=max_tokens,
            temperature=temperature,
        ),
    )
    return result.text


async def side_query_classify(
    provider: BaseProvider,
    *,
    system: str,
    prompt: str,
    labels: list[str],
    max_tokens: int = 64,
) -> str:
    """Convenience: classify text into one of the given labels.

    Returns the best-matching label, or the raw text if no match.
    """
    labels_str = ", ".join(f'"{l}"' for l in labels)
    full_prompt = (
        f"{prompt}\n\n"
        f"Respond with ONLY one of: {labels_str}\n"
        f"No explanation."
    )
    result = await side_query(
        provider,
        SideQueryOptions(
            system=system,
            messages=[user_message(full_prompt)],
            max_tokens=max_tokens,
            temperature=0.0,
        ),
    )
    text = result.text.strip().strip('"').lower()
    for label in labels:
        if label.lower() == text:
            return label
    return result.text.strip()
