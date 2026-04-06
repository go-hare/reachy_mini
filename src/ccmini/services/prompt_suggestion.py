"""Prompt Suggestion — predict what the user might type next.

Ported from Claude Code's ``PromptSuggestion`` subsystem:
- Fires after the model produces a final response (no tool calls)
- Predicts the user's next natural input (2-12 words)
- Filters out evaluative, claude-voice, and meta-text suggestions
- Includes speculation support for pre-computing the response
"""

from __future__ import annotations

import enum
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..hooks import PostSamplingHook
from ..messages import Message, user_message

if TYPE_CHECKING:
    from ..providers import BaseProvider
    from ..hooks import PostSamplingContext

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────

@dataclass(slots=True)
class PromptSuggestionConfig:
    """Configuration for prompt suggestion."""

    enabled: bool = True
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


_current_suggestion = PromptSuggestionState()


def get_current_suggestion() -> PromptSuggestionState:
    return _current_suggestion


def clear_suggestion() -> None:
    global _current_suggestion
    _current_suggestion = PromptSuggestionState()


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
        if context.query_source not in ("sdk", "repl_main_thread"):
            return

        if _has_tool_calls_in_last_assistant(context.messages):
            return

        suggestion = await generate_suggestion(
            context.messages,
            self._provider,
            self._config,
        )

        global _current_suggestion
        if suggestion:
            _current_suggestion = PromptSuggestionState(
                text=suggestion,
                generated_at=time.time(),
            )
            logger.debug("Suggestion: %s", suggestion)
        else:
            _current_suggestion = PromptSuggestionState()


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


def abort_prompt_suggestion() -> None:
    """Cancel any in-flight suggestion generation.

    Typically called when the user starts typing so we don't waste tokens.
    Also clears the current suggestion.
    """
    global _current_abort
    _current_abort = object()  # new token invalidates prior generation
    clear_suggestion()
    logger.debug("Prompt suggestion aborted")


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
