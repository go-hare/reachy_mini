"""Tool result budget — truncate oversized output.

Long tool results (file contents, search output, command stdout) can
blow up the context window.  This module clips them to a configurable
budget while preserving the most useful parts (head + tail).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from ..messages import Message, ToolResultBlock, ToolUseBlock

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    max_chars: int = 80_000
    head_ratio: float = 0.4
    tail_ratio: float = 0.4
    search_max_results: int = 50


DEFAULT_BUDGET = BudgetConfig()


def truncate_tool_result(
    text: str,
    config: BudgetConfig = DEFAULT_BUDGET,
) -> str:
    """Truncate *text* if it exceeds ``config.max_chars``.

    Keeps the first ``head_ratio`` and last ``tail_ratio`` of the
    budget, inserting a marker in the middle.
    """
    if len(text) <= config.max_chars:
        return text

    head_len = int(config.max_chars * config.head_ratio)
    tail_len = int(config.max_chars * config.tail_ratio)
    omitted = len(text) - head_len - tail_len

    return (
        text[:head_len]
        + f"\n\n[... truncated {omitted:,} characters ...]\n\n"
        + text[-tail_len:]
    )


def truncate_search_results(
    text: str,
    config: BudgetConfig = DEFAULT_BUDGET,
) -> str:
    """Specialised truncation for search/grep style output.

    Keeps the first N result lines and appends a count.
    """
    lines = text.split("\n")
    if len(lines) <= config.search_max_results:
        return truncate_tool_result(text, config)

    kept = lines[: config.search_max_results]
    omitted = len(lines) - config.search_max_results
    kept.append(f"\n[... {omitted} more lines omitted ...]")
    return truncate_tool_result("\n".join(kept), config)


def should_truncate(text: str, config: BudgetConfig = DEFAULT_BUDGET) -> bool:
    return len(text) > config.max_chars


# ── Per-tool budgets ─────────────────────────────────────────────────


_TOOL_BUDGETS: dict[str, int] = {
    "Bash": 10_000,
    "shell": 10_000,
    "Read": 30_000,
    "Grep": 5_000,
    "ripgrep": 5_000,
    "Glob": 5_000,
    "list_directory": 5_000,
    "WebFetch": 20_000,
    "WebSearch": 10_000,
    "mcp": 15_000,
}


@dataclass(frozen=True, slots=True)
class ToolBudgetConfig:
    """Per-tool budget overrides.

    Tools not in *overrides* fall back to ``_TOOL_BUDGETS`` or the
    global ``BudgetConfig.max_chars``.
    """
    overrides: dict[str, int] = field(default_factory=dict)
    fallback: BudgetConfig = field(default_factory=lambda: DEFAULT_BUDGET)

    def get_budget(self, tool_name: str) -> int:
        if tool_name in self.overrides:
            return self.overrides[tool_name]
        # Normalise: strip common prefixes/suffixes
        normalised = tool_name.lower().replace("-", "_")
        for key, val in _TOOL_BUDGETS.items():
            if normalised == key or normalised.endswith(f"_{key}"):
                return val
        return self.fallback.max_chars


DEFAULT_TOOL_BUDGET_CONFIG = ToolBudgetConfig()


def get_tool_budget(tool_name: str, config: ToolBudgetConfig = DEFAULT_TOOL_BUDGET_CONFIG) -> int:
    """Return the max-chars budget for *tool_name*."""
    return config.get_budget(tool_name)


def truncate_for_tool(
    text: str,
    tool_name: str,
    config: ToolBudgetConfig = DEFAULT_TOOL_BUDGET_CONFIG,
) -> str:
    """Truncate *text* using the per-tool budget for *tool_name*."""
    budget = config.get_budget(tool_name)
    return truncate_tool_result(text, BudgetConfig(max_chars=budget))


# ── Smart truncation ─────────────────────────────────────────────────


def detect_content_type(text: str) -> str:
    """Heuristic content-type detection for tool output.

    Returns one of ``"json"``, ``"code"``, ``"log"``, or ``"text"``.
    """
    stripped = text.lstrip()

    if stripped.startswith(("{", "[")):
        try:
            import json
            json.loads(stripped[:5_000] if len(stripped) > 5_000 else stripped)
            return "json"
        except (json.JSONDecodeError, ValueError):
            if stripped.startswith("{") and ":" in stripped[:200]:
                return "json"

    lines = text.split("\n", 20)
    if len(lines) >= 5:
        timestamp_like = sum(
            1 for ln in lines[:10]
            if any(marker in ln for marker in ["[", "ERROR", "WARN", "INFO", "DEBUG", ">>>", "---"])
        )
        if timestamp_like >= 3:
            return "log"

    code_markers = ("def ", "class ", "import ", "function ", "const ", "let ", "var ", "return ", "#include", "package ")
    code_lines = sum(1 for ln in lines if any(ln.strip().startswith(m) for m in code_markers))
    if code_lines >= 2:
        return "code"

    return "text"


def smart_truncate(text: str, max_chars: int = 80_000) -> str:
    """Truncate *text* intelligently based on detected content type.

    - **code**: keep head + tail (function signatures + recent lines).
    - **log**: keep last N lines (most recent entries are most useful).
    - **json**: truncate nested arrays/objects from the middle.
    - **text**: default head+tail truncation.
    """
    if len(text) <= max_chars:
        return text

    kind = detect_content_type(text)

    if kind == "log":
        return _truncate_log(text, max_chars)
    elif kind == "code":
        return _truncate_code(text, max_chars)
    elif kind == "json":
        return _truncate_json(text, max_chars)
    else:
        return truncate_tool_result(text, BudgetConfig(max_chars=max_chars))


def _truncate_log(text: str, max_chars: int) -> str:
    """For logs, keep the last N lines (most recent = most relevant)."""
    lines = text.split("\n")
    header_budget = int(max_chars * 0.1)
    tail_budget = max_chars - header_budget

    header_lines: list[str] = []
    header_used = 0
    for ln in lines[:5]:
        if header_used + len(ln) + 1 > header_budget:
            break
        header_lines.append(ln)
        header_used += len(ln) + 1

    tail_lines: list[str] = []
    tail_used = 0
    for ln in reversed(lines):
        if tail_used + len(ln) + 1 > tail_budget:
            break
        tail_lines.append(ln)
        tail_used += len(ln) + 1
    tail_lines.reverse()

    omitted = len(lines) - len(header_lines) - len(tail_lines)
    return (
        "\n".join(header_lines)
        + f"\n\n[... {omitted} log lines omitted ...]\n\n"
        + "\n".join(tail_lines)
    )


def _truncate_code(text: str, max_chars: int) -> str:
    """For code, keep head (imports/signatures) and tail (recent changes)."""
    head_len = int(max_chars * 0.45)
    tail_len = int(max_chars * 0.45)
    omitted = len(text) - head_len - tail_len
    return (
        text[:head_len]
        + f"\n\n[... {omitted:,} characters of code omitted ...]\n\n"
        + text[-tail_len:]
    )


def _truncate_json(text: str, max_chars: int) -> str:
    """For JSON, try to keep structure with truncated inner content."""
    try:
        import json
        data = json.loads(text)
        truncated = _truncate_json_value(data, max_depth=3, max_array_items=5)
        result = json.dumps(truncated, indent=2)
        if len(result) <= max_chars:
            return result
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return truncate_tool_result(text, BudgetConfig(max_chars=max_chars))


def _truncate_json_value(value: object, max_depth: int, max_array_items: int) -> object:
    if max_depth <= 0:
        if isinstance(value, dict):
            return {"...": f"{len(value)} keys truncated"}
        if isinstance(value, list):
            return [f"... {len(value)} items truncated"]
        if isinstance(value, str) and len(value) > 200:
            return value[:200] + "..."
        return value

    if isinstance(value, dict):
        return {
            k: _truncate_json_value(v, max_depth - 1, max_array_items)
            for k, v in list(value.items())[:20]
        }
    if isinstance(value, list):
        truncated = [
            _truncate_json_value(item, max_depth - 1, max_array_items)
            for item in value[:max_array_items]
        ]
        if len(value) > max_array_items:
            truncated.append(f"... {len(value) - max_array_items} more items")
        return truncated
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "..."
    return value


# ── Dynamic budget ───────────────────────────────────────────────────


def calculate_dynamic_budget(
    remaining_tokens: int,
    tool_count: int,
    *,
    min_budget_chars: int = 2_000,
    base_budget_chars: int = 80_000,
    chars_per_token: int = 4,
) -> int:
    """Adjust tool result budget based on remaining context window.

    When context is getting full, shrink tool budgets more aggressively
    so new tool results fit without triggering compaction.

    Parameters
    ----------
    remaining_tokens:
        Estimated tokens remaining before hitting the context limit.
    tool_count:
        Number of pending tool results to allocate budget across.
    min_budget_chars:
        Absolute floor per tool — never go below this.
    base_budget_chars:
        Budget when context is plentiful.
    chars_per_token:
        Rough char-to-token ratio.
    """
    if tool_count <= 0:
        return base_budget_chars

    remaining_chars = remaining_tokens * chars_per_token
    per_tool_chars = remaining_chars // max(1, tool_count * 2)  # reserve 50% for future turns

    budget = min(base_budget_chars, per_tool_chars)
    return max(min_budget_chars, budget)


def truncate_with_dynamic_budget(
    text: str,
    remaining_tokens: int,
    tool_count: int = 1,
    tool_name: str = "",
) -> str:
    """Truncate using a dynamically computed budget.

    Combines per-tool budgets with dynamic context-aware sizing.
    """
    static_budget = get_tool_budget(tool_name) if tool_name else DEFAULT_BUDGET.max_chars
    dynamic = calculate_dynamic_budget(remaining_tokens, tool_count)
    effective = min(static_budget, dynamic)
    return smart_truncate(text, max_chars=effective)


# ── Compact-aware truncation ─────────────────────────────────────────
#
# Ported from Claude Code's compact.ts: budgets used when restoring
# file content after compaction.

POST_COMPACT_MAX_FILES = 5
POST_COMPACT_TOKEN_BUDGET = 50_000
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000
POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000
POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000

SKILL_TRUNCATION_MARKER = (
    "\n\n[... skill content truncated for compaction; "
    "use Read on the skill path if you need the full text]"
)


def truncate_to_tokens(
    content: str,
    max_tokens: int,
    *,
    chars_per_token: int = 4,
) -> str:
    """Truncate content to roughly *max_tokens*, keeping the head.

    Ported from Claude Code's ``truncateToTokens`` in compact.ts.
    Uses ~4 chars/token (same heuristic as ``roughTokenCountEstimation``).
    """
    estimated = len(content) // chars_per_token
    if estimated <= max_tokens:
        return content
    char_budget = max_tokens * chars_per_token - len(SKILL_TRUNCATION_MARKER)
    return content[:max(0, char_budget)] + SKILL_TRUNCATION_MARKER


def truncate_for_compact(
    text: str,
    *,
    max_tokens_per_file: int = POST_COMPACT_MAX_TOKENS_PER_FILE,
    chars_per_token: int = 4,
) -> str:
    """Truncate a file read result for post-compact restoration.

    After compaction, recently-read files are re-injected so the model
    doesn't have to re-read them.  Each file is capped individually.
    """
    return truncate_to_tokens(text, max_tokens_per_file, chars_per_token=chars_per_token)


# ── File-read dedup stub detection ───────────────────────────────────

FILE_UNCHANGED_STUB = "(file content unchanged since last read)"


def is_dedup_stub(content: str) -> bool:
    """Check if a tool_result is a file-read dedup stub.

    Claude Code deduplicates file reads: if the file hasn't changed
    since the last Read, the tool_result is replaced with a short stub
    instead of the full content.  During post-compact file restoration,
    stubs should NOT prevent re-injection of the real file content.
    """
    return content.startswith(FILE_UNCHANGED_STUB)


# ── Rough token estimation ───────────────────────────────────────────


def rough_token_estimate(text: str, *, chars_per_token: int = 4) -> int:
    """Quick char-based token estimate matching Claude Code's default.

    This is intentionally fast and imprecise — suitable for budget
    checks but not billing.
    """
    return max(1, len(text) // chars_per_token)


def fits_in_budget(
    items: list[str],
    budget_tokens: int,
    *,
    chars_per_token: int = 4,
) -> list[str]:
    """Filter *items* to fit within *budget_tokens*.

    Returns items in order until the budget is exhausted.  Ported from
    the post-compact file attachment token-budget loop in compact.ts.
    """
    used = 0
    kept: list[str] = []
    for item in items:
        tokens = rough_token_estimate(item, chars_per_token=chars_per_token)
        if used + tokens > budget_tokens:
            break
        used += tokens
        kept.append(item)
    return kept


# ── Content-type-aware per-tool ratio ────────────────────────────────


_TOOL_CHARS_PER_TOKEN: dict[str, int] = {
    "Bash": 5,
    "shell": 5,
    "Read": 4,
    "Grep": 4,
    "ripgrep": 4,
    "WebFetch": 3,
    "WebSearch": 3,
    "mcp": 4,
}


def get_tool_chars_per_token(tool_name: str) -> int:
    """Return a tool-specific chars-per-token ratio.

    Log/shell output typically has more whitespace and short lines,
    so more chars per token.  Fetched web content is denser.
    """
    normalised = tool_name.lower().replace("-", "_")
    for key, val in _TOOL_CHARS_PER_TOKEN.items():
        if normalised == key or normalised.endswith(f"_{key}"):
            return val
    return 4


# ── query.ts: applyToolResultBudget (toolResultStorage.ts) ──────────

# Mirrors ``constants/toolLimits.ts`` + ``toolResultStorage.ts``.
TOOL_RESULTS_SUBDIR = "tool-results"
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
PREVIEW_SIZE_BYTES = 2000
BYTES_PER_TOKEN = 4
MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200_000

_ENV_AGGREGATE_BUDGET = "MINI_AGENT_AGGREGATE_TOOL_RESULT_BUDGET"
_ENV_PER_MESSAGE_LIMIT = "MINI_AGENT_PER_MESSAGE_TOOL_RESULT_BUDGET_CHARS"


def is_aggregate_budget_feature_enabled() -> bool:
    """Gate matching ``tengu_hawthorn_steeple`` (default off in reference)."""
    v = (os.environ.get(_ENV_AGGREGATE_BUDGET) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def get_per_message_budget_limit() -> int:
    """``getPerMessageBudgetLimit`` — env override or ``MAX_TOOL_RESULTS_PER_MESSAGE_CHARS``."""
    raw = (os.environ.get(_ENV_PER_MESSAGE_LIMIT) or "").strip()
    if raw:
        try:
            n = int(raw, 10)
            if n > 0:
                return n
        except ValueError:
            pass
    return MAX_TOOL_RESULTS_PER_MESSAGE_CHARS


@dataclass
class ContentReplacementState:
    """Per-thread state for aggregate tool-result budget (TS ``ContentReplacementState``)."""

    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, str] = field(default_factory=dict)


def create_content_replacement_state() -> ContentReplacementState:
    return ContentReplacementState()


def clone_content_replacement_state(source: ContentReplacementState) -> ContentReplacementState:
    return ContentReplacementState(
        seen_ids=set(source.seen_ids),
        replacements=dict(source.replacements),
    )


class ToolResultReplacementDict(TypedDict):
    kind: Literal["tool-result"]
    toolUseId: str
    replacement: str


def _format_file_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _generate_preview(content: str, max_bytes: int) -> tuple[str, bool]:
    if len(content) <= max_bytes:
        return content, False
    truncated = content[:max_bytes]
    last_nl = truncated.rfind("\n")
    cut = last_nl if last_nl > max_bytes * 0.5 else max_bytes
    return content[:cut], True


def build_large_tool_result_message(filepath: str, original_size: int, preview: str, has_more: bool) -> str:
    msg = f"{PERSISTED_OUTPUT_TAG}\n"
    msg += (
        f"Output too large ({_format_file_size(original_size)}). "
        f"Full output saved to: {filepath}\n\n"
    )
    msg += f"Preview (first {_format_file_size(PREVIEW_SIZE_BYTES)}):\n"
    msg += preview
    msg += "\n...\n" if has_more else "\n"
    msg += PERSISTED_OUTPUT_CLOSING_TAG
    return msg


async def _persist_tool_result_text(content: str, tool_use_id: str, tool_results_dir: Path) -> dict[str, Any] | None:
    """Write tool result to ``tool_results_dir/{id}.txt`` (exclusive create)."""

    def _write() -> dict[str, Any] | None:
        tool_results_dir.mkdir(parents=True, exist_ok=True)
        filepath = tool_results_dir / f"{tool_use_id}.txt"
        try:
            with open(filepath, "x", encoding="utf-8") as fh:
                fh.write(content)
        except FileExistsError:
            pass
        except OSError as exc:
            logger.debug("persist tool result failed: %s", exc)
            return None
        preview, has_more = _generate_preview(content, PREVIEW_SIZE_BYTES)
        return {
            "filepath": str(filepath),
            "original_size": len(content),
            "preview": preview,
            "has_more": has_more,
        }

    return await asyncio.to_thread(_write)


def _assistant_wire_id(message: Message) -> str:
    if message.role != "assistant":
        return ""
    md = message.metadata or {}
    return str(md.get("assistantId") or md.get("uuid") or "")


def _is_content_already_compacted(content: str) -> bool:
    return content.startswith(PERSISTED_OUTPUT_TAG)


def _content_size(content: str) -> int:
    return len(content)


def _build_tool_name_map(messages: list[Message]) -> dict[str, str]:
    out: dict[str, str] = {}
    for message in messages:
        if message.role != "assistant":
            continue
        blocks = message.content if isinstance(message.content, list) else []
        for block in blocks:
            if isinstance(block, ToolUseBlock):
                out[block.id] = block.name
    return out


@dataclass(frozen=True, slots=True)
class _ToolResultCandidate:
    tool_use_id: str
    content: str
    size: int


def _collect_candidates_from_message(message: Message) -> list[_ToolResultCandidate]:
    if message.role != "user" or not isinstance(message.content, list):
        return []
    out: list[_ToolResultCandidate] = []
    for block in message.content:
        if not isinstance(block, ToolResultBlock) or not block.content:
            continue
        c = block.content
        if _is_content_already_compacted(c):
            continue
        out.append(
            _ToolResultCandidate(
                tool_use_id=block.tool_use_id,
                content=c,
                size=_content_size(c),
            )
        )
    return out


def collect_candidates_by_message(messages: list[Message]) -> list[list[_ToolResultCandidate]]:
    """Group tool_result candidates like ``collectCandidatesByMessage`` in TS."""
    groups: list[list[_ToolResultCandidate]] = []
    current: list[_ToolResultCandidate] = []

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
        current = []

    seen_asst_ids: set[str] = set()
    for message in messages:
        if message.role == "user":
            current.extend(_collect_candidates_from_message(message))
        elif message.role == "assistant":
            aid = _assistant_wire_id(message) or "__missing__"
            if aid not in seen_asst_ids:
                flush()
                seen_asst_ids.add(aid)
    flush()
    return groups


@dataclass
class _CandidatePartition:
    must_reapply: list[tuple[_ToolResultCandidate, str]] = field(default_factory=list)
    frozen: list[_ToolResultCandidate] = field(default_factory=list)
    fresh: list[_ToolResultCandidate] = field(default_factory=list)


def _partition_by_prior_decision(
    candidates: list[_ToolResultCandidate],
    state: ContentReplacementState,
) -> _CandidatePartition:
    acc = _CandidatePartition()
    for c in candidates:
        rep = state.replacements.get(c.tool_use_id)
        if rep is not None:
            acc.must_reapply.append((c, rep))
        elif c.tool_use_id in state.seen_ids:
            acc.frozen.append(c)
        else:
            acc.fresh.append(c)
    return acc


def _select_fresh_to_replace(
    fresh: list[_ToolResultCandidate],
    frozen_size: int,
    limit: int,
) -> list[_ToolResultCandidate]:
    sorted_f = sorted(fresh, key=lambda x: x.size, reverse=True)
    selected: list[_ToolResultCandidate] = []
    remaining = frozen_size + sum(c.size for c in fresh)
    for c in sorted_f:
        if remaining <= limit:
            break
        selected.append(c)
        remaining -= c.size
    return selected


def _replace_tool_result_contents(
    messages: list[Message],
    replacement_map: dict[str, str],
) -> None:
    for message in messages:
        if message.role != "user" or not isinstance(message.content, list):
            continue
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                rep = replacement_map.get(block.tool_use_id)
                if rep is not None:
                    block.content = rep


async def _build_replacement(
    candidate: _ToolResultCandidate,
    tool_results_dir: Path | None,
) -> tuple[str, int] | None:
    if tool_results_dir is None:
        return None
    meta = await _persist_tool_result_text(candidate.content, candidate.tool_use_id, tool_results_dir)
    if meta is None:
        return None
    text = build_large_tool_result_message(
        meta["filepath"],
        meta["original_size"],
        meta["preview"],
        meta["has_more"],
    )
    return text, int(meta["original_size"])


async def enforce_tool_result_budget(
    messages: list[Message],
    state: ContentReplacementState,
    skip_tool_names: frozenset[str],
    *,
    tool_results_dir: Path | None,
) -> tuple[list[Message], list[ToolResultReplacementDict]]:
    """TS ``enforceToolResultBudget`` — mutates *state* and tool_result contents in *messages*."""
    candidates_by_message = collect_candidates_by_message(messages)
    name_by_tool_use_id = _build_tool_name_map(messages) if skip_tool_names else {}

    def should_skip(tid: str) -> bool:
        if not skip_tool_names:
            return False
        name = name_by_tool_use_id.get(tid, "")
        return name in skip_tool_names

    limit = get_per_message_budget_limit()
    replacement_map: dict[str, str] = {}
    to_persist: list[_ToolResultCandidate] = []
    reapplied_count = 0
    messages_over_budget = 0

    for candidates in candidates_by_message:
        part = _partition_by_prior_decision(candidates, state)
        for c, rep in part.must_reapply:
            replacement_map[c.tool_use_id] = rep
        reapplied_count += len(part.must_reapply)

        if not part.fresh:
            for c in candidates:
                state.seen_ids.add(c.tool_use_id)
            continue

        skipped = [c for c in part.fresh if should_skip(c.tool_use_id)]
        for c in skipped:
            state.seen_ids.add(c.tool_use_id)
        eligible = [c for c in part.fresh if not should_skip(c.tool_use_id)]

        frozen_size = sum(c.size for c in part.frozen)
        fresh_size = sum(c.size for c in eligible)

        selected = (
            _select_fresh_to_replace(eligible, frozen_size, limit)
            if frozen_size + fresh_size > limit
            else []
        )

        selected_ids = {c.tool_use_id for c in selected}
        for c in candidates:
            if c.tool_use_id not in selected_ids:
                state.seen_ids.add(c.tool_use_id)

        if not selected:
            continue
        messages_over_budget += 1
        to_persist.extend(selected)

    if not replacement_map and not to_persist:
        return messages, []

    fresh_pairs: list[tuple[_ToolResultCandidate, tuple[str, int] | None]] = []
    for c in to_persist:
        fresh_pairs.append((c, await _build_replacement(c, tool_results_dir)))

    newly_replaced: list[ToolResultReplacementDict] = []
    for candidate, built in fresh_pairs:
        state.seen_ids.add(candidate.tool_use_id)
        if built is None:
            continue
        content, original_size = built
        replacement_map[candidate.tool_use_id] = content
        state.replacements[candidate.tool_use_id] = content
        newly_replaced.append(
            {
                "kind": "tool-result",
                "toolUseId": candidate.tool_use_id,
                "replacement": content,
            }
        )
        _ = original_size

    if not replacement_map:
        return messages, []

    if newly_replaced:
        logger.debug(
            "Per-message budget: persisted %d tool results across %d message(s), %d re-applied",
            len(newly_replaced),
            messages_over_budget,
            reapplied_count,
        )

    _replace_tool_result_contents(messages, replacement_map)
    return messages, newly_replaced


def reconstruct_content_replacement_state(
    messages: list[Message],
    records: list[ToolResultReplacementDict],
    inherited_replacements: dict[str, str] | None = None,
) -> ContentReplacementState:
    """TS ``reconstructContentReplacementState``."""
    state = create_content_replacement_state()
    flat = [c for group in collect_candidates_by_message(messages) for c in group]
    candidate_ids = {c.tool_use_id for c in flat}
    for tid in candidate_ids:
        state.seen_ids.add(tid)
    for r in records:
        if r.get("kind") == "tool-result":
            tuid = str(r.get("toolUseId", ""))
            rep = str(r.get("replacement", ""))
            if tuid in candidate_ids:
                state.replacements[tuid] = rep
    if inherited_replacements:
        for tid, rep in inherited_replacements.items():
            if tid in candidate_ids and tid not in state.replacements:
                state.replacements[tid] = rep
    return state


def _session_safe_id(conversation_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in conversation_id)


def content_replacement_sidecar_path(session_dir: Path, conversation_id: str) -> Path:
    return session_dir / f"{_session_safe_id(conversation_id)}.content_replacements.jsonl"


def load_content_replacement_records(session_dir: Path, conversation_id: str) -> list[ToolResultReplacementDict]:
    path = content_replacement_sidecar_path(session_dir, conversation_id)
    if not path.is_file():
        return []
    out: list[ToolResultReplacementDict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if data.get("kind") == "tool-result":
                out.append(
                    {
                        "kind": "tool-result",
                        "toolUseId": str(data.get("toolUseId", "")),
                        "replacement": str(data.get("replacement", "")),
                    }
                )
    except (OSError, json.JSONDecodeError):
        logger.debug("Failed to load content replacement sidecar", exc_info=True)
    return out


def append_content_replacement_records(
    session_dir: Path,
    conversation_id: str,
    records: Iterable[ToolResultReplacementDict],
) -> None:
    path = content_replacement_sidecar_path(session_dir, conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def resolve_tool_results_dir_for_agent(agent: Any) -> Path | None:
    """Session ``…/tool-results`` directory for disk spill."""
    if not is_aggregate_budget_feature_enabled():
        return None
    store = getattr(agent, "_session_store", None)
    base = getattr(store, "session_dir", None) if store is not None else None
    cid = str(getattr(agent, "_conversation_id", "") or "")
    from ..paths import mini_agent_path

    if base is not None:
        root = Path(base)
    else:
        root = mini_agent_path("sessions")
    d = root / _session_safe_id(cid) / TOOL_RESULTS_SUBDIR
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return d


def skip_tool_names_for_aggregate_budget(tools: Iterable[Any]) -> frozenset[str]:
    """Tool names excluded from per-message aggregate budget enforcement.

    Reference ``query.ts`` passes tools where ``!Number.isFinite(maxResultSizeChars)``
    (unbounded tools like Read).  Python uses ``max_result_size_chars <= 0`` or
    non-finite as unlimited.
    """
    out: set[str] = set()
    for t in tools:
        m = getattr(t, "max_result_size_chars", 0)
        if not isinstance(m, (int, float)):
            out.add(getattr(t, "name", "") or "")
            continue
        if math.isnan(m) or math.isinf(m) or m <= 0:
            out.add(getattr(t, "name", "") or "")
    return frozenset(x for x in out if x)


async def apply_tool_result_budget(
    messages: list[Any],
    content_replacement_state: Any | None,
    *,
    write_to_transcript: Callable[[list[Any]], None] | None = None,
    skip_tool_names: frozenset[str] | None = None,
    tool_results_dir: Path | None = None,
) -> list[Any]:
    """Mirror ``applyToolResultBudget`` in ``query.ts`` / ``toolResultStorage.ts``."""
    if content_replacement_state is None:
        return messages
    if not isinstance(content_replacement_state, ContentReplacementState):
        return messages
    msgs = [m for m in messages if isinstance(m, Message)]
    if len(msgs) != len(messages):
        return messages
    sk = skip_tool_names or frozenset()
    _messages, newly = await enforce_tool_result_budget(
        msgs,
        content_replacement_state,
        sk,
        tool_results_dir=tool_results_dir,
    )
    if newly and write_to_transcript is not None:
        write_to_transcript(list(newly))
    return _messages
