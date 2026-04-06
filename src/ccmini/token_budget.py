"""Token budget management — warn and optionally stop when limits are hit.

Mirrors Claude Code's budget patterns:
- Session-level token/cost caps (--max-budget-usd)
- Per-turn output budget (+500k mode)
- Warning thresholds before hard stop
- Auto-continue nudging until budget is met
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .usage import UsageTracker, compute_cost


@dataclass
class TokenBudget:
    """Budget limits for a session."""
    max_total_tokens: int = 0
    max_output_tokens: int = 0
    max_cost_usd: float = 0.0
    warning_threshold: float = 0.8


@dataclass
class TurnTokenBudget:
    """Per-turn output token target ("+500k" mode).

    When the user specifies a token target in their message, the query
    loop auto-continues the model until the target is reached or
    diminishing returns are detected.
    """
    target_output_tokens: int = 0
    output_tokens_at_start: int = 0
    continuation_count: int = 0
    max_continuations: int = 10
    threshold_pct: float = 0.90
    last_delta_tokens: int = 0
    last_global_turn_tokens: int = 0
    diminishing_threshold: int = 500
    started_at: float = 0.0

    def __post_init__(self) -> None:
        if self.started_at == 0.0:
            import time
            self.started_at = time.monotonic()

    @property
    def tokens_this_turn(self) -> int:
        return 0

    def check(self, current_output_tokens: int) -> TurnBudgetCheckResult:
        """Check whether the turn budget is satisfied.

        Uses diminishing-returns detection ported from Claude Code's
        ``checkTokenBudget``: if the last two deltas are both below
        *diminishing_threshold* and we've had at least 3 continuations,
        stop early rather than looping with no progress.
        """
        if self.target_output_tokens <= 0:
            return TurnBudgetCheckResult(action="ok")

        turn_tokens = current_output_tokens - self.output_tokens_at_start
        pct = turn_tokens / self.target_output_tokens if self.target_output_tokens else 0

        delta_since_last = current_output_tokens - self.last_global_turn_tokens
        is_diminishing = (
            self.continuation_count >= 3
            and delta_since_last < self.diminishing_threshold
            and self.last_delta_tokens < self.diminishing_threshold
        )

        if is_diminishing:
            import time
            duration = time.monotonic() - self.started_at
            return TurnBudgetCheckResult(
                action="satisfied",
                pct=pct,
                turn_tokens=turn_tokens,
                message=f"Diminishing returns after {self.continuation_count} continuations ({duration:.1f}s)",
                diminishing_returns=True,
            )

        if pct >= self.threshold_pct:
            return TurnBudgetCheckResult(action="satisfied", pct=pct, turn_tokens=turn_tokens)

        if self.continuation_count >= self.max_continuations:
            return TurnBudgetCheckResult(
                action="exhausted",
                pct=pct,
                turn_tokens=turn_tokens,
                message=f"Reached max continuations ({self.max_continuations})",
            )

        self.last_delta_tokens = delta_since_last
        self.last_global_turn_tokens = current_output_tokens

        return TurnBudgetCheckResult(
            action="continue",
            pct=pct,
            turn_tokens=turn_tokens,
            message=_nudge_message(pct, turn_tokens, self.target_output_tokens),
        )


@dataclass
class TurnBudgetCheckResult:
    action: str = "ok"  # "ok" | "continue" | "satisfied" | "exhausted"
    pct: float = 0.0
    turn_tokens: int = 0
    message: str = ""
    diminishing_returns: bool = False


def _nudge_message(pct: float, turn_tokens: int, target: int) -> str:
    return (
        f"Stopped at {pct:.0%} of token target "
        f"({_fmt_tokens(turn_tokens)} / {_fmt_tokens(target)}). "
        f"Keep working — do not summarize."
    )


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


_TOKEN_BUDGET_RE = re.compile(
    r"\+?\s*(\d+(?:\.\d+)?)\s*([kmb])\s*(?:tokens?)?",
    re.IGNORECASE,
)

_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_token_budget(text: str) -> int | None:
    """Parse a token budget from user input like "+500k" or "spend 2M tokens".

    Returns the target output token count, or None if no budget found.
    """
    m = _TOKEN_BUDGET_RE.search(text)
    if m is None:
        return None
    value = float(m.group(1))
    multiplier = _MULTIPLIERS.get(m.group(2).lower(), 1)
    return int(value * multiplier)


class BudgetStatus:
    """Current budget consumption status."""

    def __init__(self, budget: TokenBudget, tracker: UsageTracker) -> None:
        self._budget = budget
        self._tracker = tracker

    @property
    def total_tokens_used(self) -> int:
        s = self._tracker.summary()
        return s["total_input_tokens"] + s["total_output_tokens"]

    @property
    def output_tokens_used(self) -> int:
        return self._tracker.summary()["total_output_tokens"]

    @property
    def cost_usd(self) -> float:
        return self._tracker.summary().get("total_cost_usd", 0.0)

    def is_over_limit(self) -> bool:
        b = self._budget
        if b.max_total_tokens and self.total_tokens_used >= b.max_total_tokens:
            return True
        if b.max_output_tokens and self.output_tokens_used >= b.max_output_tokens:
            return True
        if b.max_cost_usd and self.cost_usd >= b.max_cost_usd:
            return True
        return False

    def is_warning(self) -> bool:
        b = self._budget
        t = b.warning_threshold
        if b.max_total_tokens and self.total_tokens_used >= b.max_total_tokens * t:
            return True
        if b.max_cost_usd and self.cost_usd >= b.max_cost_usd * t:
            return True
        return False

    def status_text(self) -> str:
        parts: list[str] = []
        b = self._budget
        if b.max_total_tokens:
            parts.append(f"Tokens: {self.total_tokens_used}/{b.max_total_tokens}")
        if b.max_cost_usd:
            parts.append(f"Cost: ${self.cost_usd:.4f}/${b.max_cost_usd:.2f}")
        if not parts:
            return ""
        return " | ".join(parts)

    def check(self) -> BudgetCheckResult:
        if self.is_over_limit():
            return BudgetCheckResult(action="stop", message=f"Budget exceeded: {self.status_text()}")
        if self.is_warning():
            return BudgetCheckResult(action="warn", message=f"Budget warning: {self.status_text()}")
        return BudgetCheckResult(action="ok", message="")


@dataclass
class BudgetCheckResult:
    action: str = ""  # "ok" | "warn" | "stop"
    message: str = ""


# ── Prompt cache partitioning ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CachePartition:
    """Optimal cache breakpoints for Anthropic prompt caching.

    The API caches at block boundaries (system | static messages | dynamic).
    Placing breakpoints correctly maximises cache_read and minimises
    cache_creation tokens on subsequent turns.
    """
    static_tokens: int = 0
    cached_tokens: int = 0
    dynamic_tokens: int = 0

    @property
    def total(self) -> int:
        return self.static_tokens + self.cached_tokens + self.dynamic_tokens


def calculate_cache_partition(
    messages: list[Any],
    system_prompt: str | Any,
    *,
    token_estimator: Any | None = None,
) -> CachePartition:
    """Determine optimal cache breakpoints for a conversation.

    The Anthropic API caches contiguous prefixes.  We partition into:

    1. **Static** — the system prompt (rarely changes, always cached).
    2. **Cached** — older messages unlikely to change between turns.
    3. **Dynamic** — the most recent messages that change every turn.

    *token_estimator* should be ``estimate_tokens`` from the compact module;
    if *None*, a rough 4-chars-per-token heuristic is used.
    """
    from .engine.compact import estimate_tokens as _est

    est = token_estimator or _est

    if isinstance(system_prompt, str):
        static_tokens = max(1, len(system_prompt) // 4)
    else:
        static_tokens = est([system_prompt]) if callable(est) else len(str(system_prompt)) // 4

    if not messages:
        return CachePartition(static_tokens=static_tokens)

    total_msg_tokens = est(messages) if callable(est) else sum(len(str(m)) // 4 for m in messages)

    # Heuristic: the last ~20% of messages are dynamic (change every turn).
    # Everything before that is stable and should be in the cached partition.
    dynamic_count = max(1, len(messages) // 5)
    dynamic_messages = messages[-dynamic_count:]
    cached_messages = messages[:-dynamic_count] if dynamic_count < len(messages) else []

    dynamic_tokens = est(dynamic_messages) if callable(est) and dynamic_messages else 0
    cached_tokens = max(0, total_msg_tokens - dynamic_tokens)

    return CachePartition(
        static_tokens=static_tokens,
        cached_tokens=cached_tokens,
        dynamic_tokens=dynamic_tokens,
    )


# ── Extended thinking budget ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ThinkingBudget:
    """Token reservation for model's internal reasoning (extended thinking).

    When extended thinking is enabled, the model uses part of the output
    budget for internal chain-of-thought.  This dataclass captures how
    the output budget is split between thinking and visible output.
    """
    thinking_tokens: int = 0
    output_tokens: int = 0
    total_reserved: int = 0


def calculate_thinking_budget(
    context_tokens: int,
    max_output: int,
    *,
    thinking_ratio: float = 0.6,
    min_output: int = 4_000,
    max_thinking: int = 128_000,
) -> ThinkingBudget:
    """Reserve tokens for the model's internal reasoning.

    *thinking_ratio* controls the default split between thinking and
    visible output.  The split is clamped so that at least *min_output*
    tokens are available for visible text.

    Parameters
    ----------
    context_tokens:
        Estimated input/context tokens for the current request.
    max_output:
        The maximum output token budget for the model.
    thinking_ratio:
        Fraction of max_output to reserve for thinking (default 60 %).
    min_output:
        Minimum tokens guaranteed for visible output.
    max_thinking:
        Hard cap on thinking tokens regardless of ratio.
    """
    thinking_raw = int(max_output * thinking_ratio)
    thinking = min(thinking_raw, max_thinking)
    output = max(min_output, max_output - thinking)
    thinking = max_output - output  # adjust if output ate into thinking

    return ThinkingBudget(
        thinking_tokens=max(0, thinking),
        output_tokens=output,
        total_reserved=max(0, thinking) + output,
    )


# ── Per-model budget configuration ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class ModelBudget:
    """Model-specific resource limits and capabilities."""
    model_name: str
    max_context: int = 200_000
    max_output: int = 8_192
    supports_thinking: bool = False
    supports_cache: bool = True
    cache_ttl: int = 300
    cost_per_1m_input: float = 3.0
    cost_per_1m_output: float = 15.0


_MODEL_BUDGETS: dict[str, ModelBudget] = {
    "claude-sonnet-4-20250514": ModelBudget(
        model_name="claude-sonnet-4-20250514",
        max_context=200_000,
        max_output=64_000,
        supports_thinking=True,
        cache_ttl=300,
        cost_per_1m_input=3.0,
        cost_per_1m_output=15.0,
    ),
    "claude-opus-4-20250514": ModelBudget(
        model_name="claude-opus-4-20250514",
        max_context=200_000,
        max_output=64_000,
        supports_thinking=True,
        cache_ttl=300,
        cost_per_1m_input=15.0,
        cost_per_1m_output=75.0,
    ),
    "claude-sonnet-4-6-20260514": ModelBudget(
        model_name="claude-sonnet-4-6-20260514",
        max_context=1_000_000,
        max_output=128_000,
        supports_thinking=True,
        cache_ttl=300,
        cost_per_1m_input=3.0,
        cost_per_1m_output=15.0,
    ),
    "claude-opus-4-6-20260514": ModelBudget(
        model_name="claude-opus-4-6-20260514",
        max_context=1_000_000,
        max_output=128_000,
        supports_thinking=True,
        cache_ttl=300,
        cost_per_1m_input=15.0,
        cost_per_1m_output=75.0,
    ),
    "claude-haiku-4-20260514": ModelBudget(
        model_name="claude-haiku-4-20260514",
        max_context=200_000,
        max_output=64_000,
        supports_thinking=True,
        cache_ttl=300,
        cost_per_1m_input=0.8,
        cost_per_1m_output=4.0,
    ),
    "claude-3-5-haiku-20241022": ModelBudget(
        model_name="claude-3-5-haiku-20241022",
        max_context=200_000,
        max_output=8_192,
        supports_thinking=False,
        cache_ttl=300,
        cost_per_1m_input=0.8,
        cost_per_1m_output=4.0,
    ),
    "claude-3-5-sonnet-20241022": ModelBudget(
        model_name="claude-3-5-sonnet-20241022",
        max_context=200_000,
        max_output=8_192,
        supports_thinking=False,
        cache_ttl=300,
        cost_per_1m_input=3.0,
        cost_per_1m_output=15.0,
    ),
}

_BUDGET_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6-20260514",
    "opus": "claude-opus-4-6-20260514",
    "haiku": "claude-haiku-4-20260514",
    "sonnet-4": "claude-sonnet-4-20250514",
    "opus-4": "claude-opus-4-20250514",
    "sonnet-4-6": "claude-sonnet-4-6-20260514",
    "opus-4-6": "claude-opus-4-6-20260514",
    "haiku-4": "claude-haiku-4-20260514",
}

_DEFAULT_BUDGET = ModelBudget(model_name="unknown", max_context=128_000, max_output=4_096)


def get_model_budget(model_name: str) -> ModelBudget:
    """Return model-specific budget config.

    Falls back to a conservative default for unrecognised model names.
    """
    if model_name in _MODEL_BUDGETS:
        return _MODEL_BUDGETS[model_name]
    canonical = _BUDGET_ALIASES.get(model_name)
    if canonical and canonical in _MODEL_BUDGETS:
        return _MODEL_BUDGETS[canonical]
    for key in _MODEL_BUDGETS:
        if model_name.startswith(key) or key.startswith(model_name):
            return _MODEL_BUDGETS[key]
    return ModelBudget(model_name=model_name)


# ── Budget advisor ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BudgetAdvice:
    """Recommendations from the budget advisor."""
    should_compact: bool = False
    should_snip: bool = False
    should_switch_model: bool = False
    system_tokens: int = 0
    message_tokens: int = 0
    tool_tokens: int = 0
    remaining: int = 0
    recommended_model: str = ""
    reason: str = ""


class BudgetAdvisor:
    """Analyses token usage and recommends context management actions.

    Mirrors Claude Code's proactive autocompact + snip decision-making:
    before each API call, check how full the context window is and
    recommend the cheapest action that keeps us under the limit.
    """

    def __init__(
        self,
        *,
        compact_threshold: float = 0.80,
        snip_threshold: float = 0.60,
        switch_threshold: float = 0.95,
    ) -> None:
        self._compact_threshold = compact_threshold
        self._snip_threshold = snip_threshold
        self._switch_threshold = switch_threshold

    def advise(
        self,
        messages: list[Any],
        tools: list[Any] | None = None,
        config: ModelBudget | None = None,
        system_prompt: str = "",
    ) -> BudgetAdvice:
        """Analyse current token usage and return a recommendation.

        Parameters
        ----------
        messages:
            The current conversation messages.
        tools:
            Tool definitions (for estimating schema tokens).
        config:
            Model budget; defaults to a generic 200k context.
        system_prompt:
            System prompt text (for token estimation).
        """
        from .engine.compact import estimate_tokens as _est

        cfg = config or _DEFAULT_BUDGET
        max_ctx = cfg.max_context

        system_tokens = max(1, len(system_prompt) // 4)
        message_tokens = _est(messages) if messages else 0
        tool_tokens = sum(len(str(t)) // 4 for t in (tools or []))
        total = system_tokens + message_tokens + tool_tokens
        remaining = max(0, max_ctx - total)
        ratio = total / max_ctx if max_ctx > 0 else 0.0

        should_compact = ratio >= self._compact_threshold
        should_snip = ratio >= self._snip_threshold and not should_compact
        should_switch = ratio >= self._switch_threshold

        recommended = ""
        reason = ""
        if should_switch:
            recommended = "claude-sonnet-4-20250514"
            reason = f"Context {ratio:.0%} full — consider switching to a larger model"
        elif should_compact:
            reason = f"Context {ratio:.0%} full — compaction recommended"
        elif should_snip:
            reason = f"Context {ratio:.0%} full — snipping oldest messages recommended"

        return BudgetAdvice(
            should_compact=should_compact,
            should_snip=should_snip,
            should_switch_model=should_switch,
            system_tokens=system_tokens,
            message_tokens=message_tokens,
            tool_tokens=tool_tokens,
            remaining=remaining,
            recommended_model=recommended,
            reason=reason,
        )


# ── Capped output token config ──────────────────────────────────────
#
# Ported from Claude Code's context.ts: slot-reservation optimisation.
# BQ p99 output ≈ 5k tokens, so the default 32k/64k budget over-reserves
# 8-16×.  Capping the default and escalating on overflow reduces waste.


@dataclass(frozen=True, slots=True)
class CappedOutputConfig:
    """Slot-reservation optimisation for max_output_tokens.

    Start with a low cap; escalate to the model's true limit only when
    the API responds with ``model_context_window_exceeded`` or a
    ``max_tokens`` overflow error.  This is a pure cost saving — most
    responses fit well under 8k tokens.
    """
    capped_default: int = 8_000
    escalated_max: int = 64_000
    floor_output_tokens: int = 3_000

    def initial_max_tokens(self, model: str) -> int:
        budget = get_model_budget(model)
        return min(self.capped_default, budget.max_output)

    def escalated_max_tokens(self, model: str) -> int:
        budget = get_model_budget(model)
        return min(self.escalated_max, budget.max_output)

    def compute_safe_tokens(
        self,
        input_tokens: int,
        context_limit: int,
        thinking_tokens: int = 0,
        safety_buffer: int = 1_000,
    ) -> int:
        """Compute the largest safe max_output_tokens after an overflow.

        Mirrors Claude Code's ``parseMaxTokensContextOverflowError``
        retry logic: subtract input + safety, ensure thinking still fits.
        """
        available = max(0, context_limit - input_tokens - safety_buffer)
        min_required = thinking_tokens + 1
        return max(self.floor_output_tokens, available, min_required)


DEFAULT_CAPPED_OUTPUT = CappedOutputConfig()


# ── Effective context window ─────────────────────────────────────────


AUTOCOMPACT_BUFFER_TOKENS = 13_000
WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
ERROR_THRESHOLD_BUFFER_TOKENS = 20_000
MANUAL_COMPACT_BUFFER_TOKENS = 3_000
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000


def get_effective_context_window(
    model: str,
    *,
    max_summary_output: int = MAX_OUTPUT_TOKENS_FOR_SUMMARY,
) -> int:
    """Context window minus output reservation, matching Claude Code's
    ``getEffectiveContextWindowSize``.
    """
    budget = get_model_budget(model)
    reserved = min(budget.max_output, max_summary_output)
    return budget.max_context - reserved


def get_autocompact_threshold(model: str) -> int:
    """Token count above which autocompact should fire."""
    return get_effective_context_window(model) - AUTOCOMPACT_BUFFER_TOKENS


@dataclass(frozen=True, slots=True)
class ContextWarningState:
    """Mirrors Claude Code's ``calculateTokenWarningState``."""
    percent_left: int
    is_above_warning: bool
    is_above_error: bool
    is_above_autocompact: bool
    is_at_blocking_limit: bool


def calculate_context_warning_state(
    token_usage: int,
    model: str,
    *,
    autocompact_enabled: bool = True,
) -> ContextWarningState:
    """Compute warning/error thresholds for the current context usage.

    Returns a ``ContextWarningState`` with boolean flags for each
    severity level, exactly matching Claude Code's
    ``calculateTokenWarningState``.
    """
    ac_threshold = get_autocompact_threshold(model)
    effective = get_effective_context_window(model)
    threshold = ac_threshold if autocompact_enabled else effective

    percent_left = max(0, round(((threshold - token_usage) / threshold) * 100)) if threshold > 0 else 0

    warning_threshold = threshold - WARNING_THRESHOLD_BUFFER_TOKENS
    error_threshold = threshold - ERROR_THRESHOLD_BUFFER_TOKENS

    blocking_limit = effective - MANUAL_COMPACT_BUFFER_TOKENS

    return ContextWarningState(
        percent_left=percent_left,
        is_above_warning=token_usage >= warning_threshold,
        is_above_error=token_usage >= error_threshold,
        is_above_autocompact=autocompact_enabled and token_usage >= ac_threshold,
        is_at_blocking_limit=token_usage >= blocking_limit,
    )


def calculate_context_percentages(
    current_usage: dict[str, int] | None,
    context_window_size: int,
) -> dict[str, int | None]:
    """Calculate used/remaining percentages from API usage data.

    Ported from Claude Code's ``calculateContextPercentages`` in
    ``context.ts``.
    """
    if current_usage is None:
        return {"used": None, "remaining": None}

    total_input = (
        current_usage.get("input_tokens", 0)
        + current_usage.get("cache_creation_input_tokens", 0)
        + current_usage.get("cache_read_input_tokens", 0)
    )
    used_pct = min(100, max(0, round((total_input / context_window_size) * 100))) if context_window_size > 0 else 0
    return {"used": used_pct, "remaining": 100 - used_pct}


def get_model_max_output_tokens(model: str) -> dict[str, int]:
    """Return default and upper-limit max_output_tokens for *model*.

    Mirrors Claude Code's ``getModelMaxOutputTokens`` from ``context.ts``.
    """
    budget = get_model_budget(model)
    name = budget.model_name.lower()

    if "opus-4-6" in name or "sonnet-4-6" in name:
        return {"default": 64_000, "upper_limit": 128_000}
    if "opus-4-5" in name or "sonnet-4" in name or "haiku-4" in name:
        return {"default": 32_000, "upper_limit": 64_000}
    if "opus-4" in name:
        return {"default": 32_000, "upper_limit": 32_000}
    if "3-5-sonnet" in name or "3-5-haiku" in name:
        return {"default": 8_192, "upper_limit": 8_192}

    return {"default": min(32_000, budget.max_output), "upper_limit": budget.max_output}
