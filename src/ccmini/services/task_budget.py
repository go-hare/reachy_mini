"""Task budget (Anthropic ``task_budget`` beta) — remaining tokens across turns and compact.

Reference: ``taskBudgetRemaining`` survives compaction; consumption is driven by
per-call usage.  Host sets ``agent._task_budget`` to ``{"total": N, "remaining": M}``.
"""

from __future__ import annotations

from typing import Any


def apply_usage_to_task_budget(
    task_budget: dict[str, int] | None,
    *,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, int] | None:
    """Decrement ``remaining`` by this API call's token usage (input + output).

    If ``remaining`` is absent, returns *task_budget* unchanged.
    """
    if task_budget is None or "remaining" not in task_budget:
        return task_budget
    out = dict(task_budget)
    consumed = max(0, int(input_tokens) + int(output_tokens))
    rem = max(0, int(out["remaining"]) - consumed)
    out["remaining"] = rem
    return out


def attach_task_budget_to_agent(agent: Any, task_budget: dict[str, int] | None) -> None:
    """Helper for hosts that build task_budget dicts outside ``Agent``."""
    if task_budget is not None:
        agent._task_budget = dict(task_budget)  # type: ignore[attr-defined]
