"""Tool-use summary generator — one-line progress labels via a fast model.

Mirrors Claude Code's ``toolUseSummaryGenerator``: after a batch of tools
finishes, a lightweight ``side_query`` call generates a short human-readable
label like "Searched in auth/" or "Fixed NPE in UserService".

The summary is non-critical — if generation fails the caller gets ``None``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..providers import BaseProvider

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Write a short summary label describing what these tool calls accomplished. "
    "It appears as a single-line row in a UI and truncates around 30 characters, "
    "so think git-commit-subject, not sentence.\n\n"
    "Keep the verb in past tense and the most distinctive noun. "
    "Drop articles, connectors, and long location context first.\n\n"
    "Examples:\n"
    "- Searched in auth/\n"
    "- Fixed NPE in UserService\n"
    "- Created signup endpoint\n"
    "- Read config.json\n"
    "- Ran failing tests"
)


def _truncate_json(value: Any, max_length: int = 300) -> str:
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
        if len(s) <= max_length:
            return s
        return s[: max_length - 3] + "..."
    except Exception:
        return "[unable to serialize]"


async def generate_tool_use_summary(
    *,
    provider: BaseProvider,
    tools: list[dict[str, Any]],
    last_assistant_text: str = "",
) -> str | None:
    """Generate a one-line summary of completed tool calls.

    Args:
        provider: The LLM provider (ideally a fast/cheap model).
        tools: List of dicts with ``name``, ``input``, ``output`` keys.
        last_assistant_text: Optional context from the assistant's text
            preceding the tool calls.

    Returns:
        A short summary string, or None on failure.
    """
    if not tools:
        return None

    from ..delegation.fork import run_forked_side_query

    tool_parts = []
    for t in tools:
        input_str = _truncate_json(t.get("input", {}))
        output_str = _truncate_json(t.get("output", ""))
        tool_parts.append(f"Tool: {t['name']}\nInput: {input_str}\nOutput: {output_str}")

    tool_text = "\n\n".join(tool_parts)

    context_prefix = ""
    if last_assistant_text:
        context_prefix = (
            f"User's intent (from assistant's last message): "
            f"{last_assistant_text[:200]}\n\n"
        )

    prompt = f"{context_prefix}Tools completed:\n\n{tool_text}\n\nLabel:"

    try:
        result = await run_forked_side_query(
            provider=provider,
            parent_messages=[],
            system_prompt=SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=60,
            temperature=0.0,
            query_source="tool_use_summary",
        )
        summary = result.strip()
        return summary or None
    except Exception as exc:
        logger.debug("Tool use summary generation failed: %s", exc)
        return None


# ── Fast model preference ───────────────────────────────────────────


def get_summary_provider(
    default_provider: BaseProvider,
    *,
    config_model: str = "",
) -> BaseProvider:
    """Return a fast/cheap provider (haiku-class) for tool-use summaries.

    Checks ``tool_use_summary.model`` config, then falls back to the
    provider's built-in fast variant, then to *default_provider*.
    """
    if config_model:
        try:
            clone = default_provider.with_model(config_model)
            return clone
        except Exception:
            logger.debug(
                "Configured tool_use_summary model %r not available, using default",
                config_model,
            )

    try:
        fast = default_provider.get_fast_variant()
        if fast is not None:
            return fast
    except (AttributeError, NotImplementedError):
        pass

    return default_provider


# ── Abort support ───────────────────────────────────────────────────


async def generate_tool_use_summary_with_abort(
    *,
    provider: BaseProvider,
    tools: list[dict[str, Any]],
    last_assistant_text: str = "",
    abort_signal: asyncio.Event | None = None,
    config_model: str = "",
) -> str | None:
    """Like :func:`generate_tool_use_summary` but with abort + model override.

    The ``asyncio.Event`` is checked before calling the LLM.
    """
    if not tools:
        return None

    if abort_signal and abort_signal.is_set():
        return None

    effective = get_summary_provider(provider, config_model=config_model)

    return await generate_tool_use_summary(
        provider=effective,
        tools=tools,
        last_assistant_text=last_assistant_text,
    )


# ── Batch summarisation ────────────────────────────────────────────

BATCH_SYSTEM_PROMPT = (
    "Write a short summary label describing what this batch of tool calls "
    "accomplished. It appears as a single-line row in a UI and truncates "
    "around 40 characters. Use past tense.\n\n"
    "If the batch has a clear theme, use ONE label. If disparate, list "
    "up to 3 sub-labels separated by ' | '.\n\n"
    "Examples:\n"
    "- Searched auth/ and fixed UserService\n"
    "- Read 5 config files\n"
    "- Created endpoint | Updated tests | Fixed lint"
)


async def generate_batch_summary(
    *,
    provider: BaseProvider,
    tool_batches: list[list[dict[str, Any]]],
    last_assistant_text: str = "",
    abort_signal: asyncio.Event | None = None,
    config_model: str = "",
) -> str | None:
    """Summarise multiple tool calls at once — more token-efficient than
    individual summaries when many tools execute in parallel.

    *tool_batches* is a list of tool-call groups, where each group is
    a list of dicts with ``name``, ``input``, ``output`` keys.
    """
    flat_tools = [t for batch in tool_batches for t in batch]
    if not flat_tools:
        return None

    if abort_signal and abort_signal.is_set():
        return None

    effective = get_summary_provider(provider, config_model=config_model)

    from ..delegation.fork import run_forked_side_query

    tool_parts = []
    for idx, t in enumerate(flat_tools, 1):
        input_str = _truncate_json(t.get("input", {}), max_length=200)
        output_str = _truncate_json(t.get("output", ""), max_length=200)
        tool_parts.append(
            f"{idx}. {t['name']}: {input_str} → {output_str}"
        )

    tool_text = "\n".join(tool_parts)

    context_prefix = ""
    if last_assistant_text:
        context_prefix = (
            f"Intent: {last_assistant_text[:200]}\n\n"
        )

    prompt = (
        f"{context_prefix}"
        f"{len(flat_tools)} tools completed:\n{tool_text}\n\nLabel:"
    )

    try:
        result = await run_forked_side_query(
            provider=effective,
            parent_messages=[],
            system_prompt=BATCH_SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=80,
            temperature=0.0,
            query_source="tool_use_summary_batch",
        )
        summary = result.strip()
        return summary or None
    except Exception as exc:
        logger.debug("Batch tool summary generation failed: %s", exc)
        return None


# ── Non-interactive mode ────────────────────────────────────────────

NONINTERACTIVE_SYSTEM_PROMPT = (
    "Write a single-line plain-text summary of what these tool calls did. "
    "No markdown, no emoji, no UI-friendly formatting. "
    "Past tense, max 60 characters. Think log line."
)


async def generate_tool_use_summary_noninteractive(
    *,
    provider: BaseProvider,
    tools: list[dict[str, Any]],
    last_assistant_text: str = "",
    abort_signal: asyncio.Event | None = None,
    is_non_interactive: bool = False,
    config_model: str = "",
) -> str | None:
    """Generate a tool-use summary with non-interactive mode support.

    When *is_non_interactive* is True (CI / headless), uses a simplified
    plain-text format. Otherwise delegates to the standard generator.
    """
    if not tools:
        return None

    if abort_signal and abort_signal.is_set():
        return None

    if not is_non_interactive:
        return await generate_tool_use_summary_with_abort(
            provider=provider,
            tools=tools,
            last_assistant_text=last_assistant_text,
            abort_signal=abort_signal,
            config_model=config_model,
        )

    effective = get_summary_provider(provider, config_model=config_model)

    from ..delegation.fork import run_forked_side_query

    tool_parts = []
    for t in tools:
        input_str = _truncate_json(t.get("input", {}), max_length=200)
        tool_parts.append(f"- {t['name']}: {input_str}")

    prompt = (
        f"Tools:\n{''.join(tool_parts)}\n\nPlain-text label:"
    )

    try:
        result = await run_forked_side_query(
            provider=effective,
            parent_messages=[],
            system_prompt=NONINTERACTIVE_SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=40,
            temperature=0.0,
            query_source="tool_use_summary_noninteractive",
        )
        summary = result.strip()
        return summary or None
    except Exception as exc:
        logger.debug("Non-interactive tool summary failed: %s", exc)
        return None
