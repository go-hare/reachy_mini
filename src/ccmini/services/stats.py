"""Token / cost / activity tracking — port of ``utils/stats.ts``.

Tracks per-session and cumulative statistics:

- Token counts (input, output, cache reads/writes) per model
- Cost estimates based on model pricing
- Request timing and latency
- Tool call counts
- Session duration and activity

Usage::

    tracker = StatsTracker()
    tracker.record_request(model="claude-sonnet-4-20250514", input_tokens=1200, output_tokens=800)
    tracker.record_tool_call("bash")
    print(tracker.summary())
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import mini_agent_path

logger = logging.getLogger(__name__)

# ── Model pricing ($ per 1M tokens) ─────────────────────────────────

MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    "claude-3-haiku": {"input": 0.25, "output": 1.25, "cache_read": 0.03, "cache_write": 0.30},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "gpt-4o": {"input": 2.50, "output": 10.0, "cache_read": 1.25, "cache_write": 0.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_write": 0.0},
    "gpt-4.1": {"input": 2.0, "output": 8.0, "cache_read": 0.50, "cache_write": 0.0},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60, "cache_read": 0.10, "cache_write": 0.0},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40, "cache_read": 0.025, "cache_write": 0.0},
}

_DEFAULT_PRICING = {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}


def _get_pricing(model: str) -> dict[str, float]:
    """Get pricing for a model, with fuzzy matching."""
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    for key in MODEL_PRICING:
        if key in model or model in key:
            return MODEL_PRICING[key]
    return _DEFAULT_PRICING


def estimate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Estimate cost in USD for a single request."""
    pricing = _get_pricing(model)
    return (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
        + cache_read_tokens * pricing["cache_read"] / 1_000_000
        + cache_write_tokens * pricing["cache_write"] / 1_000_000
    )


# ── Per-request record ───────────────────────────────────────────────


@dataclass(slots=True)
class RequestRecord:
    timestamp: float
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    tool_calls: int = 0


# ── Per-model aggregate ──────────────────────────────────────────────


@dataclass
class ModelUsage:
    model: str = ""
    request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def avg_latency_ms(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self.total_latency_ms / self.request_count


# ── Stats tracker ────────────────────────────────────────────────────


class StatsTracker:
    """Tracks token usage, costs, and activity for a session."""

    def __init__(self, session_id: str = "") -> None:
        self._session_id = session_id
        self._start_time = time.time()
        self._requests: list[RequestRecord] = []
        self._model_usage: dict[str, ModelUsage] = {}
        self._tool_calls: dict[str, int] = {}
        self._total_cost: float = 0.0

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def session_duration_seconds(self) -> float:
        return time.time() - self._start_time

    @property
    def total_requests(self) -> int:
        return len(self._requests)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self._requests)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self._requests)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost

    @property
    def total_tool_calls(self) -> int:
        return sum(self._tool_calls.values())

    def record_request(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> RequestRecord:
        """Record a single API request."""
        cost = estimate_cost(
            model, input_tokens, output_tokens,
            cache_read_tokens, cache_write_tokens,
        )

        record = RequestRecord(
            timestamp=time.time(),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
        )
        self._requests.append(record)
        self._total_cost += cost

        if model not in self._model_usage:
            self._model_usage[model] = ModelUsage(model=model)
        mu = self._model_usage[model]
        mu.request_count += 1
        mu.input_tokens += input_tokens
        mu.output_tokens += output_tokens
        mu.cache_read_tokens += cache_read_tokens
        mu.cache_write_tokens += cache_write_tokens
        mu.total_cost_usd += cost
        mu.total_latency_ms += latency_ms

        return record

    def record_tool_call(self, tool_name: str, count: int = 1) -> None:
        """Record tool call(s)."""
        self._tool_calls[tool_name] = self._tool_calls.get(tool_name, 0) + count

    def get_model_usage(self) -> list[ModelUsage]:
        """Get per-model usage breakdown."""
        return sorted(
            self._model_usage.values(),
            key=lambda m: m.total_cost_usd,
            reverse=True,
        )

    def get_tool_usage(self) -> dict[str, int]:
        """Get tool call counts."""
        return dict(sorted(self._tool_calls.items(), key=lambda x: x[1], reverse=True))

    def summary(self, *, verbose: bool = False) -> str:
        """Generate a human-readable summary."""
        duration = self.session_duration_seconds
        lines: list[str] = [
            f"Session: {self._session_id or 'current'}",
            f"Duration: {_fmt_duration(duration)}",
            f"Requests: {self.total_requests}",
            f"Tokens: {self.total_input_tokens:,} in / {self.total_output_tokens:,} out ({self.total_tokens:,} total)",
            f"Cost: ${self._total_cost:.4f}",
            f"Tool calls: {self.total_tool_calls}",
        ]

        if verbose and self._model_usage:
            lines.append("\nPer-model breakdown:")
            for mu in self.get_model_usage():
                lines.append(
                    f"  {mu.model}: {mu.request_count} req, "
                    f"{mu.total_tokens:,} tok, "
                    f"${mu.total_cost_usd:.4f}, "
                    f"avg {mu.avg_latency_ms:.0f}ms"
                )

        if verbose and self._tool_calls:
            lines.append("\nTool usage:")
            for name, count in self.get_tool_usage().items():
                lines.append(f"  {name}: {count}")

        return "\n".join(lines)

    # ── Persistence ──────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self._session_id,
            "start_time": self._start_time,
            "total_cost_usd": self._total_cost,
            "total_requests": self.total_requests,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "tool_calls": self._tool_calls,
            "model_usage": {
                k: {
                    "request_count": v.request_count,
                    "input_tokens": v.input_tokens,
                    "output_tokens": v.output_tokens,
                    "cost_usd": v.total_cost_usd,
                }
                for k, v in self._model_usage.items()
            },
        }

    def save(self, path: Path | None = None) -> None:
        p = path or _default_stats_path(self._session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> StatsTracker:
        data = json.loads(path.read_text(encoding="utf-8"))
        tracker = cls(session_id=data.get("session_id", ""))
        tracker._start_time = data.get("start_time", time.time())
        tracker._total_cost = data.get("total_cost_usd", 0.0)
        tracker._tool_calls = data.get("tool_calls", {})
        for model, usage in data.get("model_usage", {}).items():
            tracker._model_usage[model] = ModelUsage(
                model=model,
                request_count=usage.get("request_count", 0),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                total_cost_usd=usage.get("cost_usd", 0.0),
            )
        return tracker


# ── Cumulative stats across sessions ─────────────────────────────────


def load_cumulative_stats(
    stats_dir: Path | None = None,
) -> dict[str, Any]:
    """Load and aggregate stats from all saved session files."""
    d = stats_dir or mini_agent_path("stats")
    if not d.exists():
        return {"total_cost_usd": 0.0, "total_requests": 0, "total_tokens": 0, "sessions": 0}

    total_cost = 0.0
    total_requests = 0
    total_input = 0
    total_output = 0
    sessions = 0

    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            total_cost += data.get("total_cost_usd", 0.0)
            total_requests += data.get("total_requests", 0)
            total_input += data.get("total_input_tokens", 0)
            total_output += data.get("total_output_tokens", 0)
            sessions += 1
        except Exception:
            pass

    return {
        "total_cost_usd": total_cost,
        "total_requests": total_requests,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "sessions": sessions,
    }


# ── Helpers ──────────────────────────────────────────────────────────


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def _default_stats_path(session_id: str) -> Path:
    name = session_id or "default"
    return mini_agent_path("stats", f"{name}.json")
