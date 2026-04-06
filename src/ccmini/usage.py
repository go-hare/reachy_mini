"""Token counting and cost tracking.

Every provider response carries a ``UsageRecord``.  The ``UsageTracker``
accumulates records across the session and can compute running costs
using a built-in pricing table.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class UsageRecord:
    """Token counts from a single LLM call."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    model: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# Prices per 1 million tokens: (input, output, cache_read, cache_creation)
# cache_read and cache_creation default to input price when not specified.
_ModelCost = tuple[float, float, float, float]

MODEL_COSTS: dict[str, _ModelCost] = {
    # Anthropic
    "claude-sonnet-4-20250514":       (3.00, 15.00, 0.30, 3.75),
    "claude-opus-4-20250514":        (15.00, 75.00, 1.50, 18.75),
    "claude-3-7-sonnet-20250219":    (3.00, 15.00, 0.30, 3.75),
    "claude-3-5-sonnet-20241022":    (3.00, 15.00, 0.30, 3.75),
    "claude-3-5-haiku-20241022":     (0.80,  4.00, 0.08, 1.00),
    "claude-3-haiku-20240307":       (0.25,  1.25, 0.03, 0.30),
    # OpenAI
    "gpt-4o":                        (2.50, 10.00, 1.25, 2.50),
    "gpt-4o-2024-11-20":             (2.50, 10.00, 1.25, 2.50),
    "gpt-4o-mini":                   (0.15,  0.60, 0.075, 0.15),
    "gpt-4.1":                       (2.00,  8.00, 0.50, 2.00),
    "gpt-4.1-mini":                  (0.40,  1.60, 0.10, 0.40),
    "gpt-4.1-nano":                  (0.10,  0.40, 0.025, 0.10),
    "o3":                            (2.00,  8.00, 0.50, 2.00),
    "o3-mini":                       (1.10,  4.40, 0.275, 1.10),
    "o4-mini":                       (1.10,  4.40, 0.275, 1.10),
    # DeepSeek
    "deepseek-chat":                 (0.27,  1.10, 0.07, 0.27),
    "deepseek-reasoner":             (0.55,  2.19, 0.14, 0.55),
}

# Short aliases → canonical names for fuzzy matching
_MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
    "haiku": "claude-3-5-haiku-20241022",
    "claude-3.5-sonnet": "claude-3-5-sonnet-20241022",
    "claude-3.7-sonnet": "claude-3-7-sonnet-20250219",
    "claude-4-sonnet": "claude-sonnet-4-20250514",
    "claude-4-opus": "claude-opus-4-20250514",
}


def _resolve_cost(model: str) -> _ModelCost | None:
    if model in MODEL_COSTS:
        return MODEL_COSTS[model]
    if model in _MODEL_ALIASES:
        return MODEL_COSTS.get(_MODEL_ALIASES[model])
    for key in MODEL_COSTS:
        if model.startswith(key) or key.startswith(model):
            return MODEL_COSTS[key]
    return None


def compute_cost(record: UsageRecord) -> float:
    """Compute USD cost for a single usage record."""
    costs = _resolve_cost(record.model)
    if costs is None:
        return 0.0
    inp_price, out_price, cache_read_price, cache_create_price = costs
    per_m = 1_000_000.0
    return (
        record.input_tokens * inp_price / per_m
        + record.output_tokens * out_price / per_m
        + record.cache_read_tokens * cache_read_price / per_m
        + record.cache_creation_tokens * cache_create_price / per_m
    )


class UsageTracker:
    """Accumulates usage across a session."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._total_input: int = 0
        self._total_output: int = 0
        self._total_cache_read: int = 0
        self._total_cache_create: int = 0

    def add(self, record: UsageRecord) -> None:
        self._records.append(record)
        self._total_input += record.input_tokens
        self._total_output += record.output_tokens
        self._total_cache_read += record.cache_read_tokens
        self._total_cache_create += record.cache_creation_tokens

    @property
    def total_input_tokens(self) -> int:
        return self._total_input

    @property
    def total_output_tokens(self) -> int:
        return self._total_output

    @property
    def total_tokens(self) -> int:
        return self._total_input + self._total_output

    @property
    def total_cache_read_tokens(self) -> int:
        return self._total_cache_read

    @property
    def total_cache_creation_tokens(self) -> int:
        return self._total_cache_create

    @property
    def call_count(self) -> int:
        return len(self._records)

    def total_cost(self) -> float:
        return sum(compute_cost(r) for r in self._records)

    def last_cost(self) -> float:
        if not self._records:
            return 0.0
        return compute_cost(self._records[-1])

    def summary(self) -> dict[str, Any]:
        return {
            "calls": self.call_count,
            "total_input_tokens": self._total_input,
            "total_output_tokens": self._total_output,
            "total_cache_read_tokens": self._total_cache_read,
            "total_cache_creation_tokens": self._total_cache_create,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost(), 6),
        }

    def reset(self) -> None:
        self._records.clear()
        self._total_input = 0
        self._total_output = 0
        self._total_cache_read = 0
        self._total_cache_create = 0
