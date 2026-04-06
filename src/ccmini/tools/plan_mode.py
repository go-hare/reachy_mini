"""Plan Mode Tool — model-driven planning before implementation.

Ported from Claude Code's ``EnterPlanModeTool`` / ``ExitPlanModeTool``:
- The model can proactively enter plan mode for complex tasks
- In plan mode: read-only exploration, no file writes
- The model designs an approach, presents it for approval
- On exit: plan is verified, then implementation begins

Two tools:
- ``EnterPlanModeTool`` — transitions to plan mode
- ``ExitPlanModeTool`` — presents plan and exits plan mode

Plan verification is embedded in ExitPlanMode (no separate tool needed).

Extended with:
- Session persistence (save/restore plan mode state)
- Read-only tool switching (filter tool set in plan mode)
- Plan file attachment (auto-create plan.md)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import mini_agent_home
from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)

_SESSION_DIR = mini_agent_home()
_SESSION_FILE = _SESSION_DIR / "plan_mode_state.json"


# ── Plan state ──────────────────────────────────────────────────────

@dataclass
class PlanState:
    """Tracks plan mode state."""
    is_active: bool = False
    plan_text: str = ""
    entry_turn: int = 0
    pre_permission_mode: str = "default"
    exploration_notes: list[str] = field(default_factory=list)


_plan_state = PlanState()


def get_plan_state() -> PlanState:
    return _plan_state


def reset_plan_state() -> None:
    global _plan_state
    _plan_state = PlanState()


def is_plan_mode_active() -> bool:
    return _plan_state.is_active


def enter_plan_mode(
    *,
    permission_checker: Any | None = None,
) -> str:
    """Enter plan mode and switch permissions to plan.

    Mirrors Claude Code's behavior: entering plan mode also transitions the
    active permission mode so writes are blocked until exit.
    """
    if _plan_state.is_active:
        return _plan_state.pre_permission_mode or "default"

    current_mode = "default"
    if permission_checker is not None:
        try:
            current_mode = permission_checker.mode.value
        except Exception:
            current_mode = str(getattr(permission_checker, "mode", "default"))

    _plan_state.is_active = True
    _plan_state.plan_text = _plan_state.plan_text or ""
    _plan_state.exploration_notes = []
    _plan_state.pre_permission_mode = current_mode or "default"

    if permission_checker is not None:
        try:
            from ..permissions import PermissionMode

            permission_checker.set_mode(PermissionMode.PLAN)
        except Exception:
            logger.debug("Failed to switch permission checker into plan mode", exc_info=True)

    persist_plan_mode(enabled=True)
    return _plan_state.pre_permission_mode


def exit_plan_mode(
    *,
    permission_checker: Any | None = None,
) -> str:
    """Exit plan mode and restore the pre-plan permission mode."""
    restore_mode = _plan_state.pre_permission_mode or "default"
    _plan_state.is_active = False

    if permission_checker is not None:
        try:
            from ..permissions import PermissionMode

            permission_checker.set_mode(PermissionMode(restore_mode))
        except Exception:
            logger.debug("Failed to restore permission checker after plan mode", exc_info=True)

    persist_plan_mode(enabled=False, plan_text=_plan_state.plan_text)
    return restore_mode


# ── EnterPlanMode ───────────────────────────────────────────────────

ENTER_PLAN_INSTRUCTIONS = """\
Entered plan mode. You should now focus on exploring the codebase and \
designing an implementation approach.

In plan mode:
1. Thoroughly explore the codebase to understand existing patterns
2. Identify similar features and architectural approaches
3. Consider multiple approaches and their trade-offs
4. Use AskUserQuestion if you need to clarify the approach
5. Design a concrete implementation strategy
6. When ready, use ExitPlanMode to present your plan for approval

DO NOT write or edit any files yet. This is a read-only exploration \
and planning phase."""


class EnterPlanModeTool(Tool):
    """Transitions the agent into plan mode for complex tasks.

    Use proactively when about to start a non-trivial implementation.
    Getting user sign-off prevents wasted effort and ensures alignment.
    """

    name = "EnterPlanMode"
    description = (
        "Enter plan mode for complex tasks requiring exploration and design. "
        "Use when: new features, multiple valid approaches, architectural "
        "decisions, multi-file changes, or unclear requirements. "
        "Skip for: single-line fixes, obvious implementations, pure research."
    )
    instructions = """\
Use this tool proactively when you're about to start a non-trivial task.

## When to Use
- New feature implementation (where should it go? what patterns?)
- Multiple valid approaches (Redis vs in-memory vs file-based)
- Code modifications affecting existing behavior
- Architectural decisions
- Multi-file changes (>2-3 files)
- Unclear requirements needing exploration

## When NOT to Use
- Single-line or few-line fixes
- Tasks with very specific, detailed user instructions
- Pure research/exploration (use Agent tool instead)"""
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(
        self,
        *,
        context: ToolUseContext,
        **kwargs: Any,
    ) -> str:
        if _plan_state.is_active:
            return "Already in plan mode. Use ExitPlanMode when ready."
        enter_plan_mode(
            permission_checker=context.extras.get("permission_checker"),
        )

        logger.info("Entered plan mode")
        return ENTER_PLAN_INSTRUCTIONS


# ── ExitPlanMode ────────────────────────────────────────────────────

PLAN_TEMPLATE = """\
## Implementation Plan

### Summary
{summary}

### Approach
{approach}

### Files to Modify
{files}

### Verification Steps
{verification}"""


class ExitPlanModeTool(Tool):
    """Present the implementation plan and exit plan mode.

    The plan is presented for user approval. Includes a built-in
    verification checklist (replaces the separate VerifyPlanExecution tool).
    """

    name = "ExitPlanMode"
    description = (
        "Present your implementation plan and exit plan mode. "
        "Include a summary, approach details, files to modify, "
        "and verification steps."
    )
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of what will be implemented",
                },
                "approach": {
                    "type": "string",
                    "description": "Detailed implementation approach",
                },
                "files_to_modify": {
                    "type": "string",
                    "description": "List of files that will be created or modified",
                },
                "verification_steps": {
                    "type": "string",
                    "description": "Steps to verify the implementation works",
                },
            },
            "required": ["summary", "approach"],
        }

    async def execute(
        self,
        *,
        context: ToolUseContext,
        summary: str = "",
        approach: str = "",
        files_to_modify: str = "",
        verification_steps: str = "",
        **kwargs: Any,
    ) -> str:
        if not _plan_state.is_active:
            return "Not in plan mode. Use EnterPlanMode first."

        plan = PLAN_TEMPLATE.format(
            summary=summary or "(no summary provided)",
            approach=approach or "(no approach provided)",
            files=files_to_modify or "(not specified)",
            verification=verification_steps or "(not specified)",
        )

        _plan_state.plan_text = plan
        restore_mode = exit_plan_mode(
            permission_checker=context.extras.get("permission_checker"),
        )
        _save_plan_to_file(plan)

        logger.info("Exited plan mode with plan")

        return (
            f"Plan mode complete. Here is the plan for user approval:\n\n"
            f"{plan}\n\n"
            "The plan has been presented. You may now proceed with "
            "implementation if the user approves, or revise if they "
            f"have feedback. Restored permission mode: {restore_mode}."
        )


# ── Verify Plan Execution ──────────────────────────────────────────

class VerifyPlanExecutionTool(Tool):
    """Verify that the implementation matches the plan.

    Run after completing implementation to check each planned
    item was addressed. Reports pass/fail for each verification step.
    """

    name = "VerifyPlanExecution"
    description = (
        "Verify that the implementation matches the approved plan. "
        "Run after completing all planned changes."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "verification_results": {
                    "type": "string",
                    "description": (
                        "Results of each verification step from the plan. "
                        "Format: one line per step with PASS/FAIL status."
                    ),
                },
                "deviations": {
                    "type": "string",
                    "description": "Any deviations from the original plan",
                },
                "remaining_work": {
                    "type": "string",
                    "description": "Any planned items not yet completed",
                },
            },
            "required": ["verification_results"],
        }

    async def execute(
        self,
        *,
        context: ToolUseContext,
        verification_results: str = "",
        deviations: str = "",
        remaining_work: str = "",
        **kwargs: Any,
    ) -> str:
        parts: list[str] = ["## Plan Verification Report\n"]

        parts.append(f"### Verification Results\n{verification_results}\n")

        if deviations:
            parts.append(f"### Deviations from Plan\n{deviations}\n")

        if remaining_work:
            parts.append(f"### Remaining Work\n{remaining_work}\n")

        # Check for any FAILs
        fail_count = verification_results.upper().count("FAIL")
        pass_count = verification_results.upper().count("PASS")
        total = fail_count + pass_count

        if total > 0:
            parts.append(
                f"\n**Score: {pass_count}/{total} checks passed"
                + (" — all clear!" if fail_count == 0 else f", {fail_count} need attention.**")
            )

        if _plan_state.plan_text:
            parts.append(f"\n### Original Plan\n{_plan_state.plan_text}")

        report = "\n".join(parts)
        logger.info("Plan verification: %d/%d passed", pass_count, total)
        return report


# ── Session persistence ──────────────────────────────────────────────


def persist_plan_mode(enabled: bool, plan_text: str = "") -> None:
    """Save the plan mode state to disk for session recovery."""
    try:
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "is_active": enabled,
            "plan_text": plan_text or _plan_state.plan_text,
            "pre_permission_mode": _plan_state.pre_permission_mode,
        }
        _SESSION_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to persist plan mode state: %s", exc)


def restore_plan_mode() -> None:
    """Restore plan mode state from a previous session."""
    global _plan_state
    if not _SESSION_FILE.is_file():
        return
    try:
        data = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
        _plan_state.is_active = data.get("is_active", False)
        _plan_state.plan_text = data.get("plan_text", "")
        _plan_state.pre_permission_mode = data.get("pre_permission_mode", "default")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to restore plan mode state: %s", exc)


# ── Read-only tool switching ─────────────────────────────────────────

_READ_ONLY_TOOL_NAMES = frozenset({
    "Glob", "Grep", "Read", "WebFetch", "WebSearch",
    "EnterPlanMode", "ExitPlanMode", "VerifyPlanExecution",
    "TodoWrite", "ToolSearch",
})


def get_plan_mode_tools(all_tools: list[Tool]) -> list[Tool]:
    """Filter *all_tools* to only those safe for plan mode (read-only).

    When the agent is in plan mode it should not be able to write files,
    run destructive commands, or spawn background tasks. This function
    returns the subset of tools that are read-only plus the plan-mode
    tools themselves.
    """
    return [
        t for t in all_tools
        if t.is_read_only or t.name in _READ_ONLY_TOOL_NAMES
    ]


# ── Plan file attachment ─────────────────────────────────────────────


def _save_plan_to_file(plan_text: str, *, path: str | Path | None = None) -> Path:
    """Write the plan to ``plan.md`` in the project root (cwd).

    Returns the path that was written.
    """
    target = Path(path) if path else Path.cwd() / "plan.md"
    try:
        target.write_text(plan_text, encoding="utf-8")
        logger.info("Plan saved to %s", target)
    except OSError as exc:
        logger.warning("Could not save plan to %s: %s", target, exc)
    return target
