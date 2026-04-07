"""Prompt Suggestion — predict what the user might type next.

Ported from Claude Code's ``PromptSuggestion`` subsystem:
- Fires after the model produces a final response (no tool calls)
- Predicts the user's next natural input (2-12 words)
- Filters out evaluative, claude-voice, and meta-text suggestions
- Includes speculation support for pre-computing the response
"""

from __future__ import annotations

import asyncio
import ast
import contextlib
import enum
import logging
import os
import copy
import re
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..hooks import PostSamplingHook
from ..messages import (
    CompletionEvent,
    ErrorEvent,
    Message,
    PromptSuggestionEvent,
    SpeculationEvent,
    StreamEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolUseBlock,
    user_message,
)

if TYPE_CHECKING:
    from ..providers import BaseProvider
    from ..hooks import PostSamplingContext

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────

@dataclass(slots=True)
class PromptSuggestionConfig:
    """Configuration for prompt suggestion."""

    enabled: bool = True
    speculation_enabled: bool = True
    min_assistant_turns: int = 2
    max_suggestion_words: int = 12
    max_suggestion_chars: int = 100


DEFAULT_CONFIG = PromptSuggestionConfig()


# ── Suggestion prompt ───────────────────────────────────────────────

SUGGESTION_PROMPT = """\
[SUGGESTION MODE: Suggest what the user might naturally type next.]

FIRST: Look at the user's recent messages and original request.

Your job is to predict what THEY would type — not what you think they should do.

THE TEST: Would they think "I was just about to type that"?

EXAMPLES:
User asked "fix the bug and run tests", bug is fixed → "run the tests"
After code written → "try it out"
Claude offers options → suggest the one the user would likely pick
Claude asks to continue → "yes" or "go ahead"
Task complete, obvious follow-up → "commit this" or "push it"
After error or misunderstanding → silence (let them assess/correct)

Be specific: "run the tests" beats "continue".

NEVER SUGGEST:
- Evaluative ("looks good", "thanks")
- Questions ("what about...?")
- Claude-voice ("Let me...", "I'll...", "Here's...")
- New ideas they didn't ask about
- Multiple sentences

Stay silent if the next step isn't obvious from what the user said.

Format: 2-12 words, match the user's style. Or nothing.

Reply with ONLY the suggestion, no quotes or explanation."""


# ── State ───────────────────────────────────────────────────────────

@dataclass
class PromptSuggestionState:
    """Current suggestion state."""

    text: str = ""
    generated_at: float = 0.0
    shown_at: float = 0.0
    accepted_at: float = 0.0


@dataclass
class SpeculationBoundary:
    """Boundary reached while running speculation."""

    type: str = ""
    tool_name: str = ""
    detail: str = ""
    file_path: str = ""
    completed_at: float = 0.0


@dataclass
class SpeculationState:
    """Best-effort prefetch state for a predicted next user input."""

    status: str = "idle"
    suggestion: str = ""
    reply: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    error: str = ""
    boundary: SpeculationBoundary = field(default_factory=SpeculationBoundary)
    events: list[StreamEvent] = field(default_factory=list, repr=False)
    added_messages: list[Message] = field(default_factory=list, repr=False)
    stop_reason: str = ""
    workspace_root: str = ""
    overlay_dir: str = ""
    written_paths: list[str] = field(default_factory=list)


_current_suggestion = PromptSuggestionState()
_current_speculation = SpeculationState()
_current_speculation_task: asyncio.Task[Any] | None = None
_current_speculation_abort: asyncio.Event | None = None


def _get_agent_prompt_state(agent: Any | None) -> PromptSuggestionState | None:
    state = getattr(agent, "_prompt_suggestion_state", None)
    return state if isinstance(state, PromptSuggestionState) else None


def _set_prompt_state(state: PromptSuggestionState, *, agent: Any | None = None) -> None:
    global _current_suggestion
    _current_suggestion = state
    if agent is not None:
        setattr(agent, "_prompt_suggestion_state", state)


def _get_agent_speculation_state(agent: Any | None) -> SpeculationState | None:
    state = getattr(agent, "_speculation_state", None)
    return state if isinstance(state, SpeculationState) else None


def _set_speculation_state(state: SpeculationState, *, agent: Any | None = None) -> None:
    global _current_speculation
    _current_speculation = state
    if agent is not None:
        setattr(agent, "_speculation_state", state)


def _get_speculation_task(agent: Any | None) -> asyncio.Task[Any] | None:
    task = getattr(agent, "_speculation_task", None)
    if isinstance(task, asyncio.Task):
        return task
    return _current_speculation_task


def _set_speculation_task(
    task: asyncio.Task[Any] | None,
    *,
    agent: Any | None = None,
) -> None:
    global _current_speculation_task
    _current_speculation_task = task
    if agent is not None:
        setattr(agent, "_speculation_task", task)


def _get_speculation_abort(agent: Any | None) -> asyncio.Event | None:
    abort_event = getattr(agent, "_speculation_abort_event", None)
    if isinstance(abort_event, asyncio.Event):
        return abort_event
    return _current_speculation_abort


def _set_speculation_abort(
    abort_event: asyncio.Event | None,
    *,
    agent: Any | None = None,
) -> None:
    global _current_speculation_abort
    _current_speculation_abort = abort_event
    if agent is not None:
        setattr(agent, "_speculation_abort_event", abort_event)


def _emit_agent_stream_event(agent: Any | None, event: StreamEvent) -> None:
    if agent is None:
        return
    queue = getattr(agent, "_event_queue", None)
    if queue is not None and hasattr(queue, "put_nowait"):
        try:
            queue.put_nowait(event)
        except Exception:
            logger.debug("Failed to queue runtime prompt/speculation event", exc_info=True)
    fire = getattr(agent, "_fire_event", None)
    if callable(fire):
        try:
            fire(event)
        except Exception:
            logger.debug("Failed to publish runtime prompt/speculation event", exc_info=True)


def _emit_prompt_suggestion_event(agent: Any | None, state: PromptSuggestionState) -> None:
    _emit_agent_stream_event(
        agent,
        PromptSuggestionEvent(
            text=state.text,
            shown_at=state.shown_at or state.generated_at,
            accepted_at=state.accepted_at,
        ),
    )


def _emit_speculation_event(agent: Any | None, state: SpeculationState) -> None:
    _emit_agent_stream_event(
        agent,
        SpeculationEvent(
            status=state.status,
            suggestion=state.suggestion,
            reply=state.reply,
            started_at=state.started_at,
            completed_at=state.completed_at,
            error=state.error,
            boundary={
                "type": state.boundary.type,
                "tool_name": state.boundary.tool_name,
                "detail": state.boundary.detail,
                "file_path": state.boundary.file_path,
                "completed_at": state.boundary.completed_at,
            },
        ),
    )


def get_current_suggestion(agent: Any | None = None) -> PromptSuggestionState:
    return _get_agent_prompt_state(agent) or _current_suggestion


def clear_suggestion(agent: Any | None = None) -> None:
    state = PromptSuggestionState()
    _set_prompt_state(state, agent=agent)
    _emit_prompt_suggestion_event(agent, state)


def get_current_speculation(agent: Any | None = None) -> SpeculationState:
    return _get_agent_speculation_state(agent) or _current_speculation


def clear_speculation(
    agent: Any | None = None,
    *,
    cleanup_overlay: bool = True,
) -> None:
    previous = get_current_speculation(agent)
    if cleanup_overlay:
        _cleanup_speculation_overlay(previous)
    state = SpeculationState()
    _set_speculation_state(state, agent=agent)
    _emit_speculation_event(agent, state)


def abort_speculation(agent: Any | None = None) -> None:
    """Cancel any in-flight speculation prefetch."""
    abort_event = _get_speculation_abort(agent)
    if abort_event is not None:
        abort_event.set()
    task = _get_speculation_task(agent)
    if task is not None and not task.done():
        task.cancel()
    _set_speculation_task(None, agent=agent)
    _set_speculation_abort(None, agent=agent)
    clear_speculation(agent)


# ── Suppression checks ─────────────────────────────────────────────

@dataclass(slots=True)
class AppStateSnapshot:
    """Minimal view of app state for suppression checks.

    Callers populate this from whatever state store they use. Each field
    defaults to the "nothing pending" value so that callers only set the
    fields they know about.
    """

    pending_worker_task: bool = False
    active_sandbox: bool = False
    elicitation_in_progress: bool = False
    plan_mode_active: bool = False
    rate_limited: bool = False
    user_last_typed_at: float = 0.0
    debounce_ms: float = 300.0


def get_suppression_reason(
    messages: list[Message],
    config: PromptSuggestionConfig = DEFAULT_CONFIG,
    *,
    app_state: AppStateSnapshot | None = None,
) -> str | None:
    """Return a suppression reason if suggestion should not be generated.

    Checks are ordered cheapest-first.  A non-``None`` return is the
    specific reason string; ``None`` means generation is allowed.
    """
    if not config.enabled:
        return "disabled"

    assistant_turns = sum(1 for m in messages if m.role == "assistant")
    if assistant_turns < config.min_assistant_turns:
        return "early_conversation"

    # Check if last assistant message was an error
    for msg in reversed(messages):
        if msg.role == "assistant":
            if msg.metadata.get("is_error"):
                return "last_response_error"
            break

    # App-state-driven suppression (ported from CC's getSuggestionSuppressReason)
    if app_state is not None:
        if app_state.pending_worker_task:
            return "pending_worker"
        if app_state.active_sandbox:
            return "active_sandbox"
        if app_state.elicitation_in_progress:
            return "elicitation_active"
        if app_state.plan_mode_active:
            return "plan_mode"
        if app_state.rate_limited:
            return "rate_limited"
        if app_state.user_last_typed_at > 0:
            elapsed_ms = (time.time() - app_state.user_last_typed_at) * 1000
            if elapsed_ms < app_state.debounce_ms:
                return "user_just_typed"

    return None


# ── Filter ──────────────────────────────────────────────────────────

ALLOWED_SINGLE_WORDS = frozenset({
    "yes", "yeah", "yep", "yea", "yup", "sure", "ok", "okay",
    "push", "commit", "deploy", "stop", "continue", "check", "exit", "quit",
    "no",
})

_EVALUATIVE_RE = re.compile(
    r"thanks|thank you|looks good|sounds good|that works|that worked|"
    r"that's all|nice|great|perfect|makes sense|awesome|excellent",
    re.IGNORECASE,
)

_CLAUDE_VOICE_RE = re.compile(
    r"^(let me|i'll|i've|i'm|i can|i would|i think|i notice|here's|"
    r"here is|here are|that's|this is|this will|you can|you should|"
    r"you could|sure,|of course|certainly)",
    re.IGNORECASE,
)


def should_filter_suggestion(
    suggestion: str,
    config: PromptSuggestionConfig = DEFAULT_CONFIG,
    *,
    previous_suggestion: str = "",
) -> str | None:
    """Return filter reason if suggestion should be discarded, else None.

    Enhanced filters (in addition to the originals):
    - ``contains_error``: leaked API key / error strings
    - ``too_short``: under 10 characters
    - ``too_long_chars``: over 500 characters
    - ``duplicate``: identical to the previous suggestion
    - ``contains_code_block``: suggestions should be natural language
    """
    if not suggestion:
        return "empty"

    lower = suggestion.lower()
    words = suggestion.strip().split()
    word_count = len(words)

    # Direct matches
    if lower == "done":
        return "done"

    # Meta-text
    if any(lower.startswith(p) for p in (
        "nothing found", "nothing to suggest", "no suggestion",
    )):
        return "meta_text"
    if re.search(r"\bsilence is\b|\bstay(s|ing)? silent\b", lower):
        return "meta_text"
    if re.match(r"^\W*silence\W*$", lower):
        return "meta_text"

    # Wrapped in parens/brackets
    if re.match(r"^\(.*\)$|^\[.*\]$", suggestion):
        return "meta_wrapped"

    # Error messages leaked (expanded)
    if any(lower.startswith(p) for p in (
        "api error:", "prompt is too long", "request timed out",
        "invalid api key", "image was too large",
    )):
        return "error_message"
    if "error" in lower and any(k in lower for k in (
        "api key", "rate limit", "quota", "unauthorized", "forbidden",
    )):
        return "contains_error"

    # Prefixed label (e.g., "Suggestion: ...")
    if re.match(r"^\w+:\s", suggestion):
        return "prefixed_label"

    # Too short (absolute character check)
    if len(suggestion.strip()) < 10:
        if not suggestion.startswith("/") and lower not in ALLOWED_SINGLE_WORDS:
            return "too_short"

    # Word count checks
    if word_count < 2:
        if suggestion.startswith("/"):
            pass  # slash commands OK
        elif lower not in ALLOWED_SINGLE_WORDS:
            return "too_few_words"

    if word_count > config.max_suggestion_words:
        return "too_many_words"

    if len(suggestion) >= config.max_suggestion_chars:
        return "too_long"

    # Absolute character ceiling (for suggestions that sneak past word count)
    if len(suggestion) > 500:
        return "too_long_chars"

    # Multiple sentences
    if re.search(r"[.!?]\s+[A-Z]", suggestion):
        return "multiple_sentences"

    # Formatting
    if re.search(r"[\n*]|\*\*", suggestion):
        return "has_formatting"

    # Code blocks — suggestions should be natural language
    if "```" in suggestion or re.search(r"`[^`]+`", suggestion):
        return "contains_code_block"

    # Evaluative
    if _EVALUATIVE_RE.search(lower):
        return "evaluative"

    # Claude voice
    if _CLAUDE_VOICE_RE.match(suggestion):
        return "claude_voice"

    # Duplicate of previous suggestion
    if previous_suggestion and suggestion.strip() == previous_suggestion.strip():
        return "duplicate"

    return None


# ── Generation ──────────────────────────────────────────────────────

async def generate_suggestion(
    messages: list[Message],
    provider: BaseProvider,
    config: PromptSuggestionConfig = DEFAULT_CONFIG,
) -> str | None:
    """Generate a prompt suggestion based on conversation context.

    Returns the suggestion text or None if suppressed/empty.
    """
    reason = get_suppression_reason(messages, config)
    if reason:
        logger.debug("Suggestion suppressed: %s", reason)
        return None

    # Build recent conversation for context
    recent_parts: list[str] = []
    for msg in messages[-20:]:
        text = msg.text.strip()[:1500]
        if text:
            recent_parts.append(f"[{msg.role.upper()}]: {text}")
    conversation = "\n\n".join(recent_parts)

    full_prompt = f"{conversation}\n\n---\n\n{SUGGESTION_PROMPT}"

    from ..delegation.fork import run_forked_side_query

    try:
        result = await run_forked_side_query(
            provider=provider,
            parent_messages=messages[-20:],
            system_prompt="",
            prompt=full_prompt,
            max_tokens=64,
            temperature=0.3,
            query_source="prompt_suggestion",
        )

        suggestion = result.strip()
        if not suggestion:
            return None

        # Strip quotes if model wraps
        if (suggestion.startswith('"') and suggestion.endswith('"')) or \
           (suggestion.startswith("'") and suggestion.endswith("'")):
            suggestion = suggestion[1:-1].strip()

        filter_reason = should_filter_suggestion(suggestion, config)
        if filter_reason:
            logger.debug(
                "Suggestion filtered (%s): %s", filter_reason, suggestion,
            )
            return None

        return suggestion

    except Exception as exc:
        logger.debug("Suggestion generation failed: %s", exc)
        return None


# ── Hook integration ────────────────────────────────────────────────

def _has_tool_calls_in_last_assistant(messages: list[Message]) -> bool:
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.has_tool_use
    return False


def _build_app_state_from_agent(agent: Any | None) -> AppStateSnapshot | None:
    """Build prompt-suggestion suppression state from the active runtime."""
    if agent is None:
        return None

    pending_worker_task = False
    try:
        runner = getattr(agent, "background_runner", None)
        if runner is not None and callable(getattr(runner, "list_active", None)):
            pending_worker_task = bool(runner.list_active())
    except Exception:
        logger.debug("Failed to inspect background runner state", exc_info=True)

    plan_mode_active = False
    try:
        from ..tools.plan_mode import is_plan_mode_active

        plan_mode_active = bool(is_plan_mode_active())
    except Exception:
        logger.debug("Failed to inspect plan mode state", exc_info=True)

    return AppStateSnapshot(
        pending_worker_task=pending_worker_task,
        elicitation_in_progress=bool(getattr(agent, "_pending_client_run_id", None)),
        plan_mode_active=plan_mode_active,
        user_last_typed_at=float(getattr(agent, "_last_user_activity_at", 0.0) or 0.0),
    )


def _is_teammate_agent(agent: Any | None) -> bool:
    if agent is None:
        return False
    if getattr(agent, "_identity", None) is not None:
        return True
    agent_id = str(
        getattr(agent, "_agent_id", getattr(agent, "agent_id", "")) or ""
    ).strip()
    return "@" in agent_id if agent_id else False


def _should_enable_prompt_suggestion(
    context: PostSamplingContext,
    agent: Any | None,
    config: PromptSuggestionConfig,
) -> bool:
    if not config.enabled:
        return False
    if context.query_source != "repl_main_thread":
        return False
    if bool(getattr(agent, "_runtime_is_non_interactive", False)):
        return False
    if _is_teammate_agent(agent):
        return False
    return True


def _get_last_assistant_usage(messages: list[Message]) -> dict[str, int]:
    for msg in reversed(messages):
        if msg.role != "assistant":
            continue
        usage = msg.metadata.get("usage")
        if not isinstance(usage, dict):
            return {}
        return {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "cache_creation_tokens": int(usage.get("cache_creation_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
        }
    return {}


class PromptSuggestionHook(PostSamplingHook):
    """Post-sampling hook that generates prompt suggestions.

    Fires when the model finishes a response (no pending tool calls)
    and sets ``_current_suggestion`` for the UI to display.
    """

    def __init__(
        self,
        provider: BaseProvider,
        *,
        config: PromptSuggestionConfig | None = None,
    ) -> None:
        self._provider = provider
        self._config = config or DEFAULT_CONFIG

    async def on_post_sampling(
        self,
        context: PostSamplingContext,
        *,
        agent: Any = None,
    ) -> None:
        if not _should_enable_prompt_suggestion(context, agent, self._config):
            return

        if _has_tool_calls_in_last_assistant(context.messages):
            return

        previous_suggestion = get_current_suggestion(agent).text
        usage = _get_last_assistant_usage(context.messages)
        suggestion = await generate_suggestion_v2(
            context.messages,
            self._provider,
            self._config,
            app_state=_build_app_state_from_agent(agent),
            previous_suggestion=previous_suggestion,
            parent_input_tokens=usage.get("input_tokens", 0),
            parent_cache_write_tokens=usage.get("cache_creation_tokens", 0),
            parent_output_tokens=usage.get("output_tokens", 0),
        )

        if suggestion:
            state = PromptSuggestionState(
                text=suggestion,
                generated_at=time.time(),
                shown_at=time.time(),
            )
            _set_prompt_state(state, agent=agent)
            _emit_prompt_suggestion_event(agent, state)
            logger.debug("Suggestion: %s", suggestion)
            if self._config.speculation_enabled:
                start_speculation(
                    context.messages,
                    context.system_prompt,
                    self._provider,
                    suggestion,
                    agent=agent,
                )
            else:
                abort_speculation(agent)
        else:
            clear_suggestion(agent)
            abort_speculation(agent)


# ── Suggestion outcome tracking ─────────────────────────────────────

class SuggestionOutcome(enum.Enum):
    """Possible outcomes after a suggestion is generated."""

    ACCEPTED = "accepted"
    IGNORED = "ignored"
    FILTERED = "filtered"
    SUPPRESSED = "suppressed"
    ERROR = "error"


@dataclass
class SuggestionOutcomeLog:
    """Accumulated statistics for suggestion outcomes."""

    total: int = 0
    accepted: int = 0
    ignored: int = 0
    filtered: int = 0
    suppressed: int = 0
    errors: int = 0
    _history: list[dict[str, Any]] = field(default_factory=list, repr=False)

    @property
    def accept_rate(self) -> float:
        return self.accepted / self.total if self.total > 0 else 0.0

    @property
    def filter_rate(self) -> float:
        return self.filtered / self.total if self.total > 0 else 0.0


_outcome_log = SuggestionOutcomeLog()


def get_outcome_log() -> SuggestionOutcomeLog:
    return _outcome_log


def log_suggestion_outcome(
    outcome: SuggestionOutcome,
    suggestion_text: str = "",
    reason: str = "",
) -> None:
    """Record a suggestion outcome for analytics."""
    log = _outcome_log
    log.total += 1

    if outcome == SuggestionOutcome.ACCEPTED:
        log.accepted += 1
    elif outcome == SuggestionOutcome.IGNORED:
        log.ignored += 1
    elif outcome == SuggestionOutcome.FILTERED:
        log.filtered += 1
    elif outcome == SuggestionOutcome.SUPPRESSED:
        log.suppressed += 1
    elif outcome == SuggestionOutcome.ERROR:
        log.errors += 1

    log._history.append({
        "outcome": outcome.value,
        "suggestion": suggestion_text[:100],
        "reason": reason,
        "time": time.time(),
    })

    logger.debug(
        "Suggestion outcome: %s (reason=%s, accept_rate=%.1f%%)",
        outcome.value, reason or "-", log.accept_rate * 100,
    )


# ── Prompt variants (A/B testing) ──────────────────────────────────

PromptVariant = str  # type alias for variant identifiers

PROMPT_VARIANTS: dict[str, str] = {
    "user_intent": SUGGESTION_PROMPT,
    "stated_intent": SUGGESTION_PROMPT,
}

_active_variant: str = "user_intent"


def get_prompt_variant(*, config_variant: str = "") -> str:
    """Return the active prompt variant name.

    If *config_variant* is provided and exists in ``PROMPT_VARIANTS``, it
    wins. Otherwise falls back to the module-level default.
    """
    if config_variant and config_variant in PROMPT_VARIANTS:
        return config_variant
    return _active_variant


def register_prompt_variant(name: str, prompt_text: str) -> None:
    """Register an experimental prompt variant for A/B testing."""
    PROMPT_VARIANTS[name] = prompt_text


def get_prompt_for_variant(variant: str = "") -> str:
    """Return the prompt text for a variant (default if empty/unknown)."""
    return PROMPT_VARIANTS.get(variant or _active_variant, SUGGESTION_PROMPT)


# ── Abort support ───────────────────────────────────────────────────

_current_abort: object | None = None  # opaque cancel token


def abort_prompt_suggestion(agent: Any | None = None) -> None:
    """Cancel any in-flight suggestion generation.

    Typically called when the user starts typing so we don't waste tokens.
    Also clears the current suggestion.
    """
    global _current_abort
    _current_abort = object()  # new token invalidates prior generation
    clear_suggestion(agent)
    logger.debug("Prompt suggestion aborted")


_SPECULATION_READ_ONLY_TOOL_NAMES = frozenset({
    "Read",
    "Grep",
    "Glob",
    "LSP",
    "ToolSearch",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "WebFetch",
    "WebSearch",
})
_SPECULATION_WRITE_TOOL_NAMES = frozenset({
    "Write",
    "Edit",
    "FileWrite",
    "FileEdit",
})
_SPECULATION_PATH_TOOL_NAMES = _SPECULATION_READ_ONLY_TOOL_NAMES | _SPECULATION_WRITE_TOOL_NAMES | {"NotebookEdit"}
_SPECULATION_SHELL_TOOL_NAMES = frozenset({"Bash", "PowerShell", "REPL"})
_SPECULATION_SAFE_BASH_COMMANDS = frozenset({
    "cat",
    "date",
    "df",
    "du",
    "env",
    "fd",
    "fdfind",
    "file",
    "find",
    "git",
    "grep",
    "head",
    "ls",
    "printenv",
    "pwd",
    "rg",
    "sed",
    "sort",
    "stat",
    "tail",
    "tr",
    "uniq",
    "uname",
    "wc",
    "which",
    "whoami",
})
_SPECULATION_SAFE_GIT_SUBCOMMANDS = frozenset({
    "blame",
    "describe",
    "diff",
    "grep",
    "log",
    "ls-files",
    "merge-base",
    "reflog",
    "rev-parse",
    "show",
    "status",
})
_SPECULATION_SAFE_POWERSHELL_NAVIGATION = frozenset({
    "cd",
    "chdir",
    "pop-location",
    "push-location",
    "set-location",
    "sl",
})
_SPECULATION_SAFE_POWERSHELL_EXTERNALS = frozenset({
    "git",
})


class _SpeculationBoundaryReached(RuntimeError):
    """Raised to stop speculation once a boundary tool is reached."""


def _cleanup_speculation_overlay(state: SpeculationState) -> None:
    overlay_dir = str(state.overlay_dir or "").strip()
    if not overlay_dir:
        return
    with contextlib.suppress(Exception):
        shutil.rmtree(overlay_dir, ignore_errors=True)


def _commit_speculation_overlay(state: SpeculationState) -> None:
    workspace_root = str(state.workspace_root or "").strip()
    overlay_dir = str(state.overlay_dir or "").strip()
    if not workspace_root or not overlay_dir or not state.written_paths:
        return
    root = Path(workspace_root).resolve()
    overlay_root = Path(overlay_dir).resolve()
    for rel in state.written_paths:
        rel_path = Path(rel)
        source = (overlay_root / rel_path).resolve()
        target = (root / rel_path).resolve()
        if not source.exists() or not source.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _resolve_speculation_path(raw_path: str, workspace_root: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (Path(workspace_root) / candidate).resolve()


def _relative_speculation_path(path: Path, workspace_root: str) -> str | None:
    try:
        relative = path.resolve().relative_to(Path(workspace_root).resolve())
    except ValueError:
        return None
    return relative.as_posix()


def _ensure_overlay_file(
    source: Path,
    *,
    workspace_root: str,
    overlay_dir: str,
    written_paths: set[str],
) -> Path | None:
    relative = _relative_speculation_path(source, workspace_root)
    if relative is None:
        return None
    overlay_path = (Path(overlay_dir) / Path(relative)).resolve()
    if relative not in written_paths:
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            shutil.copy2(source, overlay_path)
        written_paths.add(relative)
    return overlay_path


def _speculation_tool_mode(tool_name: str, input_data: dict[str, Any], *, is_read_only: bool) -> str:
    if tool_name == "Bash":
        command = str(input_data.get("command", "") or "").strip()
        if _is_speculation_safe_bash(command):
            return "read"
        return "boundary"
    if tool_name == "PowerShell":
        command = str(input_data.get("command", "") or "").strip()
        if _is_speculation_safe_powershell(command):
            return "read"
        return "boundary"
    if tool_name == "REPL":
        return _speculation_repl_mode(input_data)
    if tool_name in _SPECULATION_SHELL_TOOL_NAMES:
        return "boundary"
    if tool_name == "NotebookEdit":
        action = str(input_data.get("action", "") or "").strip()
        if action in {"get_cell", "list_cells"}:
            return "read"
        return "write"
    if tool_name in _SPECULATION_WRITE_TOOL_NAMES:
        return "write"
    if tool_name in _SPECULATION_READ_ONLY_TOOL_NAMES:
        return "read"
    if is_read_only:
        return "pass"
    return "boundary"


def _is_speculation_safe_bash(command: str) -> bool:
    if not command.strip():
        return False
    try:
        from ..permissions import BashCommandAnalyzer, RiskLevel

        risk, _reason = BashCommandAnalyzer.classify(command)
        return risk == RiskLevel.SAFE
    except Exception:
        split_result = _split_shell_command_for_speculation(command)
        if split_result is None:
            return False
        segments, _operators = split_result
        return all(_is_speculation_safe_bash_segment(segment) for segment in segments)


def _split_shell_command_for_speculation(command: str) -> tuple[list[str], list[str]] | None:
    segments: list[str] = []
    operators: list[str] = []
    current: list[str] = []
    quote = ""
    escape = False
    index = 0

    while index < len(command):
        char = command[index]
        if escape:
            current.append(char)
            escape = False
            index += 1
            continue

        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"':
                escape = True
            index += 1
            continue

        if char == "\\":
            current.append(char)
            escape = True
            index += 1
            continue

        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue

        if char in {"<", ">"}:
            return None
        if char == ";":
            return None
        if char == "&":
            if index + 1 < len(command) and command[index + 1] == "&":
                segment = "".join(current).strip()
                if not segment:
                    return None
                segments.append(segment)
                operators.append("&&")
                current = []
                index += 2
                continue
            return None
        if char == "|":
            if index + 1 < len(command) and command[index + 1] == "|":
                return None
            segment = "".join(current).strip()
            if not segment:
                return None
            segments.append(segment)
            operators.append("|")
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    if quote or escape:
        return None

    tail = "".join(current).strip()
    if not tail:
        return None
    segments.append(tail)
    return segments, operators


def _is_speculation_safe_bash_segment(segment: str) -> bool:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False

    lowered = [token.lower() for token in tokens]
    command = lowered[0]
    if command not in _SPECULATION_SAFE_BASH_COMMANDS:
        return False
    if command == "git":
        return _is_speculation_safe_git_tokens(lowered[1:])
    if command == "sed":
        return not any(token == "-i" or token.startswith("-i") for token in lowered[1:])
    if any(
        token in {
            "apply_patch",
            "bash",
            "node",
            "npm",
            "perl",
            "php",
            "pip",
            "pip3",
            "pnpm",
            "python",
            "python2",
            "python3",
            "ruby",
            "sh",
            "xargs",
            "yarn",
            "zsh",
        }
        for token in lowered
    ):
        return False
    return True


def _is_speculation_safe_git_tokens(args: list[str]) -> bool:
    if not args:
        return False
    subcommand = args[0]
    if subcommand not in _SPECULATION_SAFE_GIT_SUBCOMMANDS:
        return False
    blocked_flags = {
        "--exec-path",
        "--git-dir",
        "--output",
        "--work-tree",
    }
    return not any(
        token in blocked_flags or any(token.startswith(f"{flag}=") for flag in blocked_flags)
        for token in args[1:]
    )


def _is_speculation_safe_powershell(command: str) -> bool:
    if not command.strip():
        return False
    try:
        from ..tools.powershell import CmdletRisk, classify_command

        risk, _reason = classify_command(command)
        if risk == CmdletRisk.SAFE:
            return True

        split_result = _split_shell_command_for_speculation(command)
        if split_result is None:
            return False

        segments, _operators = split_result
        for segment in segments:
            try:
                tokens = shlex.split(segment, posix=False)
            except ValueError:
                return False
            if not tokens:
                return False

            head = tokens[0].strip("&").strip().strip('"').strip("'")
            lowered_head = head.lower()
            if lowered_head in _SPECULATION_SAFE_POWERSHELL_NAVIGATION:
                continue
            if lowered_head in _SPECULATION_SAFE_POWERSHELL_EXTERNALS:
                if not _is_speculation_safe_git_tokens([token.lower() for token in tokens[1:]]):
                    return False
                continue
            segment_risk, _segment_reason = classify_command(segment)
            if segment_risk != CmdletRisk.SAFE:
                return False
        return True
    except Exception:
        return False


def _speculation_repl_mode(input_data: dict[str, Any]) -> str:
    action = str(input_data.get("action", "") or "execute").strip()
    if action == "list_sessions":
        return "read"
    if action in {"execute", "execute_in_session"} and _is_speculation_safe_repl_read(input_data):
        return "read"
    return "boundary"


def _is_speculation_safe_repl_read(input_data: dict[str, Any]) -> bool:
    action = str(input_data.get("action", "") or "execute").strip()
    if action not in {"execute", "execute_in_session"}:
        return False

    code = str(input_data.get("code", "") or "")
    if not code.strip():
        return False

    language = str(input_data.get("language", "") or "").strip().lower()
    if not language:
        try:
            from ..tools.repl import _detect_language

            language = _detect_language(code)
        except Exception:
            return False
    if language != "python":
        return False
    return _is_speculation_safe_python_repl_code(code)


_SAFE_REPL_PYTHON_CALLS = frozenset({
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "float",
    "int",
    "len",
    "list",
    "max",
    "min",
    "print",
    "repr",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "type",
})

_SAFE_REPL_PYTHON_NODES = (
    ast.Add,
    ast.And,
    ast.BinOp,
    ast.BitAnd,
    ast.BitOr,
    ast.BitXor,
    ast.BoolOp,
    ast.Call,
    ast.Compare,
    ast.Constant,
    ast.Dict,
    ast.Div,
    ast.Eq,
    ast.Expr,
    ast.FloorDiv,
    ast.Gt,
    ast.GtE,
    ast.IfExp,
    ast.In,
    ast.Index if hasattr(ast, "Index") else ast.Slice,
    ast.Invert,
    ast.Is,
    ast.IsNot,
    ast.List,
    ast.Load,
    ast.Lt,
    ast.LtE,
    ast.Mod,
    ast.Module,
    ast.Mult,
    ast.Name,
    ast.Not,
    ast.NotEq,
    ast.NotIn,
    ast.Or,
    ast.Pow,
    ast.Set,
    ast.Slice,
    ast.Sub,
    ast.Subscript,
    ast.Tuple,
    ast.UAdd,
    ast.UnaryOp,
    ast.USub,
    ast.keyword,
)


class _SafeReplPythonVisitor(ast.NodeVisitor):
    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, _SAFE_REPL_PYTHON_NODES):
            raise ValueError(f"Unsupported node: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Module(self, node: ast.Module) -> None:
        if not node.body or any(not isinstance(statement, ast.Expr) for statement in node.body):
            raise ValueError("Only expression statements are allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_REPL_PYTHON_CALLS:
            raise ValueError("Only pure builtin calls are allowed")
        for keyword in node.keywords:
            if keyword.arg is None:
                raise ValueError("Starred keyword arguments are not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        raise ValueError("Attribute access is not allowed")

    def visit_Lambda(self, node: ast.Lambda) -> None:
        raise ValueError("Lambda is not allowed")

    def visit_ListComp(self, node: ast.ListComp) -> None:
        raise ValueError("Comprehensions are not allowed")

    def visit_SetComp(self, node: ast.SetComp) -> None:
        raise ValueError("Comprehensions are not allowed")

    def visit_DictComp(self, node: ast.DictComp) -> None:
        raise ValueError("Comprehensions are not allowed")

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        raise ValueError("Generator expressions are not allowed")

    def visit_FormattedValue(self, node: ast.FormattedValue) -> None:
        raise ValueError("Formatted strings are not allowed")

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        raise ValueError("Formatted strings are not allowed")


def _is_speculation_safe_python_repl_code(code: str) -> bool:
    try:
        tree = ast.parse(code, mode="exec")
        _SafeReplPythonVisitor().visit(tree)
    except Exception:
        return False
    return True


def _speculation_can_auto_accept_edits(agent: Any | None) -> bool:
    checker = getattr(agent, "_permission_checker", None)
    mode = getattr(checker, "mode", None)
    try:
        from ..permissions import PermissionMode

        return mode in {PermissionMode.ACCEPT_EDITS, PermissionMode.BYPASS}
    except Exception:
        return False


def _speculation_boundary_type(tool_name: str) -> str:
    if tool_name in {"Edit", "Write", "NotebookEdit", "FileEdit", "FileWrite"}:
        return "edit"
    if tool_name in {"Bash", "PowerShell", "REPL"}:
        return "shell"
    return "tool"


def _extract_boundary_fields(tool_name: str, input_data: dict[str, Any]) -> SpeculationBoundary:
    detail = ""
    for key in ("command", "file_path", "path", "notebook_path", "pattern", "url"):
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            detail = value.strip()
            break
    file_path = ""
    for key in ("file_path", "path", "notebook_path"):
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            file_path = value.strip()
            break
    return SpeculationBoundary(
        type=_speculation_boundary_type(tool_name),
        tool_name=tool_name,
        detail=detail[:200],
        file_path=file_path,
        completed_at=time.time(),
    )


def _sanitize_blocked_speculation_output(
    *,
    boundary: SpeculationBoundary,
    events: list[StreamEvent],
    added_messages: list[Message],
) -> tuple[list[StreamEvent], list[Message]]:
    boundary_tool_use_id = ""
    for event in reversed(events):
        if isinstance(event, ToolCallEvent) and event.tool_name == boundary.tool_name:
            boundary_tool_use_id = event.tool_use_id
            break

    cleaned_events: list[StreamEvent] = []
    for event in events:
        if isinstance(event, ErrorEvent):
            continue
        if boundary_tool_use_id and isinstance(event, ToolCallEvent) and event.tool_use_id == boundary_tool_use_id:
            continue
        if boundary_tool_use_id and isinstance(event, ToolResultEvent) and event.tool_use_id == boundary_tool_use_id:
            continue
        cleaned_events.append(event)

    cleaned_messages = copy.deepcopy(added_messages)
    if boundary_tool_use_id:
        if cleaned_messages and cleaned_messages[-1].role == "user":
            blocks = list(cleaned_messages[-1].tool_result_blocks())
            if blocks and all(block.tool_use_id == boundary_tool_use_id for block in blocks):
                cleaned_messages.pop()
        for index in range(len(cleaned_messages) - 1, -1, -1):
            message = cleaned_messages[index]
            if message.role != "assistant" or not isinstance(message.content, list):
                continue
            retained = [
                block
                for block in message.content
                if not (
                    isinstance(block, ToolUseBlock)
                    and block.id == boundary_tool_use_id
                )
            ]
            if retained != list(message.content):
                if retained:
                    message.content = retained
                else:
                    cleaned_messages.pop(index)
                break

    return cleaned_events, cleaned_messages


def _make_speculation_tools(
    agent: Any | None,
    *,
    workspace_root: str,
    overlay_dir: str,
    written_paths: set[str],
) -> list[Any]:
    from ..tool import Tool, ToolUseContext

    available = list(getattr(agent, "_tools", []) or [])
    if not available:
        return []

    tracker: dict[str, SpeculationBoundary] = {"boundary": SpeculationBoundary()}

    class _BoundaryTool(Tool):
        def __init__(self, wrapped: Tool) -> None:
            self._wrapped = wrapped
            self.name = wrapped.name
            self.aliases = getattr(wrapped, "aliases", ())
            self.description = wrapped.description
            self.instructions = wrapped.instructions
            self.is_read_only = getattr(wrapped, "is_read_only", False)
            self.supports_streaming = getattr(wrapped, "supports_streaming", False)
            self._mode = _speculation_tool_mode(
                wrapped.name,
                {},
                is_read_only=bool(getattr(wrapped, "is_read_only", False)),
            )

        def get_parameters_schema(self) -> dict[str, Any]:
            return self._wrapped.get_parameters_schema()

        def _rewrite_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
            mode = _speculation_tool_mode(
                self.name,
                kwargs,
                is_read_only=bool(getattr(self._wrapped, "is_read_only", False)),
            )
            if mode == "pass":
                return dict(kwargs)

            if mode == "boundary":
                tracker["boundary"] = _extract_boundary_fields(self.name, kwargs)
                raise _SpeculationBoundaryReached(
                    f"Speculation paused at {self.name}; continue this request in the main session."
                )

            path_key = next(
                (
                    key
                    for key in ("notebook_path", "path", "file_path")
                    if isinstance(kwargs.get(key), str) and str(kwargs.get(key)).strip()
                ),
                "",
            )
            if not path_key:
                if mode == "read":
                    return dict(kwargs)
                tracker["boundary"] = _extract_boundary_fields(self.name, kwargs)
                raise _SpeculationBoundaryReached(
                    f"Speculation paused at {self.name}; pathless write is not isolated."
                )

            source_path = _resolve_speculation_path(str(kwargs[path_key]), workspace_root)
            relative = _relative_speculation_path(source_path, workspace_root)
            if relative is None:
                if mode == "read":
                    return dict(kwargs)
                tracker["boundary"] = _extract_boundary_fields(self.name, kwargs)
                raise _SpeculationBoundaryReached(
                    f"Speculation paused at {self.name}; write outside workspace is not allowed."
                )

            rewritten = dict(kwargs)
            if mode == "write":
                if not _speculation_can_auto_accept_edits(agent):
                    tracker["boundary"] = _extract_boundary_fields(self.name, kwargs)
                    raise _SpeculationBoundaryReached(
                        f"Speculation paused at {self.name}; file edits require an auto-accept edit mode."
                    )
                overlay_path = _ensure_overlay_file(
                    source_path,
                    workspace_root=workspace_root,
                    overlay_dir=overlay_dir,
                    written_paths=written_paths,
                )
                if overlay_path is None:
                    tracker["boundary"] = _extract_boundary_fields(self.name, kwargs)
                    raise _SpeculationBoundaryReached(
                        f"Speculation paused at {self.name}; write outside workspace is not allowed."
                    )
                rewritten[path_key] = str(overlay_path)
            elif relative in written_paths:
                rewritten[path_key] = str((Path(overlay_dir) / Path(relative)).resolve())
            return rewritten

        async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
            abort_event = getattr(context, "abort_event", None)
            try:
                rewritten = self._rewrite_kwargs(kwargs)
            except _SpeculationBoundaryReached:
                if abort_event is not None and hasattr(abort_event, "set"):
                    abort_event.set()
                raise
            return await self._wrapped.execute(context=context, **rewritten)

        async def stream_execute(self, *, context: ToolUseContext, **kwargs: Any):
            abort_event = getattr(context, "abort_event", None)
            try:
                rewritten = self._rewrite_kwargs(kwargs)
            except _SpeculationBoundaryReached:
                if abort_event is not None and hasattr(abort_event, "set"):
                    abort_event.set()
                raise
            if getattr(self._wrapped, "supports_streaming", False):
                async for item in self._wrapped.stream_execute(context=context, **rewritten):
                    yield item
                return
            yield await self._wrapped.execute(context=context, **rewritten)

    speculation_tools: list[Any] = []
    for tool in available:
        speculation_tools.append(_BoundaryTool(tool))

    if agent is not None:
        setattr(agent, "_speculation_boundary_tracker", tracker)
    return speculation_tools


def start_speculation(
    messages: list[Message],
    system_prompt: str,
    provider: BaseProvider,
    suggestion: str,
    *,
    agent: Any | None = None,
) -> None:
    """Best-effort forked-agent prefetch of the predicted next input."""
    if not suggestion.strip():
        abort_speculation(agent)
        return

    abort_speculation(agent)
    abort_event = asyncio.Event()
    _set_speculation_abort(abort_event, agent=agent)
    workspace_root = str(getattr(agent, "_working_directory", "") or os.getcwd())
    overlay_dir = tempfile.mkdtemp(prefix="ccmini-spec-")
    written_paths: set[str] = set()
    running_state = SpeculationState(
        status="running",
        suggestion=suggestion,
        started_at=time.time(),
        workspace_root=workspace_root,
        overlay_dir=overlay_dir,
    )
    _set_speculation_state(running_state, agent=agent)
    _emit_speculation_event(agent, running_state)

    async def _run() -> None:
        from ..delegation.subagent import ForkedAgentContext, run_forked_agent

        try:
            speculation_tools = _make_speculation_tools(
                agent,
                workspace_root=workspace_root,
                overlay_dir=overlay_dir,
                written_paths=written_paths,
            )
            result = await run_forked_agent(
                context=ForkedAgentContext(
                    parent_messages=messages[-20:],
                    parent_system_prompt=system_prompt,
                    abort_signal=abort_event,
                ),
                provider=provider,
                tools=speculation_tools,
                max_turns=5,
                query_source="prompt_suggestion_speculation",
                fork_label="speculation",
                skip_transcript=True,
                working_directory=str(getattr(agent, "_working_directory", "") or ""),
            )
            if abort_event.is_set() and not result.aborted:
                return

            boundary_tracker = getattr(agent, "_speculation_boundary_tracker", {})
            boundary = boundary_tracker.get("boundary", SpeculationBoundary())
            if boundary.type:
                cleaned_events, cleaned_messages = _sanitize_blocked_speculation_output(
                    boundary=boundary,
                    events=list(result.events),
                    added_messages=list(result.added_messages),
                )
                state = SpeculationState(
                    status="blocked",
                    suggestion=suggestion,
                    reply=result.text.strip(),
                    started_at=running_state.started_at,
                    completed_at=time.time(),
                    boundary=boundary,
                    events=cleaned_events,
                    added_messages=cleaned_messages,
                    stop_reason=result.stop_reason,
                    workspace_root=workspace_root,
                    overlay_dir=overlay_dir,
                    written_paths=sorted(written_paths),
                )
            else:
                state = SpeculationState(
                    status="ready" if result.text.strip() else "idle",
                    suggestion=suggestion,
                    reply=result.text.strip(),
                    started_at=running_state.started_at,
                    completed_at=time.time(),
                    events=list(result.events),
                    added_messages=list(result.added_messages),
                    stop_reason=result.stop_reason,
                    workspace_root=workspace_root,
                    overlay_dir=overlay_dir,
                    written_paths=sorted(written_paths),
                )
            _set_speculation_state(state, agent=agent)
            _emit_speculation_event(agent, state)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if abort_event.is_set():
                return
            state = SpeculationState(
                status="error",
                suggestion=suggestion,
                started_at=running_state.started_at,
                completed_at=time.time(),
                error=str(exc),
            )
            _set_speculation_state(state, agent=agent)
            _emit_speculation_event(agent, state)
            logger.debug("Speculation prefetch failed: %s", exc)
        finally:
            if _get_speculation_abort(agent) is abort_event:
                _set_speculation_abort(None, agent=agent)

    task = asyncio.create_task(_run())
    _set_speculation_task(task, agent=agent)
    task.add_done_callback(
        lambda finished: _set_speculation_task(None, agent=agent)
        if _get_speculation_task(agent) is finished
        else None
    )


@dataclass
class AcceptedSpeculation:
    """Speculation result ready to replay into the main session."""

    suggestion: str
    reply: str
    events: list[StreamEvent]
    added_messages: list[Message]
    workspace_root: str
    overlay_dir: str
    written_paths: list[str]
    needs_continuation: bool = False


def try_accept_speculation(
    user_text: str,
    *,
    agent: Any | None = None,
) -> AcceptedSpeculation | None:
    """Consume a ready speculation when the user types the predicted input."""

    state = get_current_speculation(agent)
    suggestion = state.suggestion.strip()
    if not suggestion or state.status not in {"ready", "blocked"}:
        return None
    if user_text.strip() != suggestion:
        return None

    prompt_state = get_current_suggestion(agent)
    accepted_state = PromptSuggestionState(
        text="",
        generated_at=prompt_state.generated_at,
        shown_at=prompt_state.shown_at or prompt_state.generated_at,
        accepted_at=time.time(),
    )
    _set_prompt_state(accepted_state, agent=agent)
    _emit_prompt_suggestion_event(agent, accepted_state)

    accepted = AcceptedSpeculation(
        suggestion=suggestion,
        reply=state.reply,
        events=list(state.events),
        added_messages=list(state.added_messages),
        workspace_root=state.workspace_root,
        overlay_dir=state.overlay_dir,
        written_paths=list(state.written_paths),
        needs_continuation=state.status == "blocked",
    )
    clear_speculation(agent, cleanup_overlay=False)
    return accepted


def _is_aborted(token: object | None) -> bool:
    """Return True if *token* is stale (a newer abort was issued)."""
    return token is not _current_abort


# ── Cache sharing awareness ─────────────────────────────────────────

MAX_PARENT_UNCACHED_TOKENS = 8000


def get_cache_suppress_reason(
    *,
    input_tokens: int = 0,
    cache_write_tokens: int = 0,
    output_tokens: int = 0,
) -> str | None:
    """Suppress suggestion when parent messages have too many uncached tokens.

    The suggestion fork re-processes the parent's output (never cached)
    plus its own prompt.  If that budget is already large, a suggestion
    won't cache well and wastes tokens.
    """
    total = input_tokens + cache_write_tokens + output_tokens
    if total > MAX_PARENT_UNCACHED_TOKENS:
        return "cache_cold"
    return None


# ── Enhanced generation with abort + variants ───────────────────────

async def generate_suggestion_v2(
    messages: list[Message],
    provider: BaseProvider,
    config: PromptSuggestionConfig = DEFAULT_CONFIG,
    *,
    app_state: AppStateSnapshot | None = None,
    variant: str = "",
    previous_suggestion: str = "",
    parent_input_tokens: int = 0,
    parent_cache_write_tokens: int = 0,
    parent_output_tokens: int = 0,
) -> str | None:
    """Enhanced suggestion generation with abort, variants, and cache checks.

    Wraps the original ``generate_suggestion`` with additional guards:
    - abort token checking
    - cache suppression
    - prompt variant selection
    - duplicate filtering against *previous_suggestion*
    """
    global _current_abort
    my_token = _current_abort = object()

    # Cache suppress check
    cache_reason = get_cache_suppress_reason(
        input_tokens=parent_input_tokens,
        cache_write_tokens=parent_cache_write_tokens,
        output_tokens=parent_output_tokens,
    )
    if cache_reason:
        log_suggestion_outcome(
            SuggestionOutcome.SUPPRESSED, reason=cache_reason,
        )
        return None

    # Standard suppression
    reason = get_suppression_reason(messages, config, app_state=app_state)
    if reason:
        log_suggestion_outcome(SuggestionOutcome.SUPPRESSED, reason=reason)
        logger.debug("Suggestion suppressed: %s", reason)
        return None

    if _is_aborted(my_token):
        log_suggestion_outcome(SuggestionOutcome.SUPPRESSED, reason="aborted")
        return None

    # Select prompt variant
    active_variant = get_prompt_variant(config_variant=variant)
    prompt_text = get_prompt_for_variant(active_variant)

    # Build recent conversation for context
    recent_parts: list[str] = []
    for msg in messages[-20:]:
        text = msg.text.strip()[:1500]
        if text:
            recent_parts.append(f"[{msg.role.upper()}]: {text}")
    conversation = "\n\n".join(recent_parts)

    full_prompt = f"{conversation}\n\n---\n\n{prompt_text}"

    from ..delegation.fork import run_forked_side_query

    try:
        result = await run_forked_side_query(
            provider=provider,
            parent_messages=messages[-20:],
            system_prompt="",
            prompt=full_prompt,
            max_tokens=64,
            temperature=0.3,
            query_source="prompt_suggestion",
        )

        if _is_aborted(my_token):
            log_suggestion_outcome(
                SuggestionOutcome.SUPPRESSED, reason="aborted_during_gen",
            )
            return None

        suggestion = result.strip()
        if not suggestion:
            log_suggestion_outcome(SuggestionOutcome.FILTERED, reason="empty")
            return None

        # Strip quotes if model wraps
        if (suggestion.startswith('"') and suggestion.endswith('"')) or \
           (suggestion.startswith("'") and suggestion.endswith("'")):
            suggestion = suggestion[1:-1].strip()

        filter_reason = should_filter_suggestion(
            suggestion, config, previous_suggestion=previous_suggestion,
        )
        if filter_reason:
            log_suggestion_outcome(
                SuggestionOutcome.FILTERED, suggestion, filter_reason,
            )
            logger.debug(
                "Suggestion filtered (%s): %s", filter_reason, suggestion,
            )
            return None

        return suggestion

    except Exception as exc:
        log_suggestion_outcome(SuggestionOutcome.ERROR, reason=str(exc))
        logger.debug("Suggestion generation failed: %s", exc)
        return None
