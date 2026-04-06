"""Bundled Skills — built-in skills for self-repair and common workflows.

Ported from Claude Code's ``skills/bundled/`` subsystem:
- **verify** — verify code changes work by running tests/app
- **debug** — systematic debugging workflow
- **stuck** — diagnose frozen/stuck sessions
- **batch** — parallel work orchestration
- **simplify** — code review for quality and efficiency

These skills are registered automatically and available without
any user-side skill file installation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Skill definition ────────────────────────────────────────────────

@dataclass
class BundledSkillDefinition:
    """A bundled skill that ships with mini_agent."""

    name: str
    description: str
    when_to_use: str
    prompt: str
    tags: list[str] = field(default_factory=list)
    allowed_tools: list[str] | None = None
    priority: int = 5
    enabled: bool = True


# ── Registry ────────────────────────────────────────────────────────

_registry: dict[str, BundledSkillDefinition] = {}


def register_bundled_skill(skill: BundledSkillDefinition) -> None:
    _registry[skill.name] = skill


def get_bundled_skills() -> dict[str, BundledSkillDefinition]:
    return dict(_registry)


def get_bundled_skill(name: str) -> BundledSkillDefinition | None:
    return _registry.get(name)


# ── Built-in skills ────────────────────────────────────────────────

VERIFY_SKILL = BundledSkillDefinition(
    name="verify",
    description="Verify that code changes work correctly",
    when_to_use=(
        "After making code changes, to verify they work. "
        "Run tests, check for errors, validate behavior."
    ),
    tags=["test", "verify", "check", "validate"],
    prompt="""\
# Verify Changes

You are verifying that recent code changes work correctly.

## Steps

1. **Identify what changed** — review the recent modifications
2. **Find relevant tests** — look for existing test files
3. **Run tests** — execute the test suite for affected code
4. **Check for errors** — look for linter errors, type errors, import failures
5. **Validate behavior** — if no tests exist, verify by running the code

## Guidelines

- Run the most specific tests first (unit tests for changed files)
- If tests fail, identify whether the failure is pre-existing or from your changes
- Report results clearly: what passed, what failed, what needs attention
- If no automated tests exist, suggest what manual verification the user should do
- Do NOT fix failures unless explicitly asked — just report them

## Output Format

```
Verification Results:
- [PASS/FAIL] Description of check
- [PASS/FAIL] Description of check
...

Summary: X/Y checks passed. [Any issues found.]
```""",
)

DEBUG_SKILL = BundledSkillDefinition(
    name="debug",
    description="Systematic debugging workflow",
    when_to_use=(
        "When something isn't working and you need to investigate. "
        "Errors, unexpected behavior, failed tests."
    ),
    tags=["debug", "error", "fix", "investigate", "broken"],
    prompt="""\
# Debug

You are systematically debugging an issue.

## Approach

1. **Reproduce** — understand and reproduce the problem
2. **Isolate** — narrow down where the issue occurs
3. **Hypothesize** — form theories about the root cause
4. **Test** — verify each hypothesis
5. **Fix** — apply the minimal correct fix
6. **Verify** — confirm the fix resolves the issue without side effects

## Guidelines

- Start with the error message/symptoms — read them carefully
- Check recent changes that might have introduced the issue
- Use grep/glob to find related code
- Read the actual source code, don't guess
- Form multiple hypotheses and test the most likely first
- Make the smallest possible fix
- After fixing, run relevant tests to verify

## Common Patterns

- **Import errors**: check file paths, circular imports, missing dependencies
- **Type errors**: check function signatures, return types, None handling
- **Logic errors**: add logging/prints to trace execution flow
- **Config errors**: check environment variables, file paths, permissions
- **Race conditions**: check async/await patterns, shared state""",
)

STUCK_SKILL = BundledSkillDefinition(
    name="stuck",
    description="Diagnose and recover from stuck/frozen situations",
    when_to_use=(
        "When you've been going in circles, retrying the same approach, "
        "or can't make progress on a task."
    ),
    tags=["stuck", "frozen", "loop", "retry", "help"],
    prompt="""\
# Stuck Recovery

You appear to be stuck. Step back and reassess.

## Self-Diagnosis

1. **What am I trying to do?** — State the goal clearly
2. **What have I tried?** — List approaches already attempted
3. **Why isn't it working?** — Identify the specific blocker
4. **What assumptions am I making?** — Question each one

## Recovery Strategies

### Change Approach
- If you've been editing code, try reading more first
- If you've been reading, try a different search strategy
- If the approach is fundamentally wrong, start fresh

### Simplify
- Can you solve a simpler version of the problem first?
- Can you break it into smaller independent steps?
- Can you use a known-working pattern instead?

### Get Context
- Read the error message again, carefully
- Check if there's documentation you haven't read
- Look at how similar problems are solved elsewhere in the codebase

### Ask for Help
- If you genuinely can't solve it, explain what you've tried
- Present the specific blocker to the user
- Suggest alternative approaches they might prefer

## Rules
- Do NOT retry the same approach that already failed
- Do NOT make random changes hoping something works
- DO explain your reasoning when changing strategy""",
)

BATCH_SKILL = BundledSkillDefinition(
    name="batch",
    description="Parallel work orchestration across multiple files/tasks",
    when_to_use=(
        "When you need to apply similar changes across many files, "
        "or perform multiple independent tasks in parallel."
    ),
    tags=["batch", "parallel", "multi", "bulk", "mass"],
    prompt="""\
# Batch Operations

You are performing batch operations across multiple targets.

## Planning

1. **List all targets** — enumerate every file/item to process
2. **Define the operation** — what exactly needs to happen to each
3. **Identify dependencies** — which operations depend on others?
4. **Order execution** — independent items first, dependent items after

## Execution Strategy

- **Read all targets first** before making any changes
- **Group by similarity** — handle identical patterns together
- **Use parallel tools** — read operations can run concurrently
- **Track progress** — maintain a checklist of completed items
- **Verify after each group** — catch errors early

## Guidelines

- Never modify a file you haven't read in this session
- If one operation fails, continue with others (don't block the batch)
- Report partial progress if the batch is large
- For >10 targets, consider using grep/glob to verify changes""",
)

SIMPLIFY_SKILL = BundledSkillDefinition(
    name="simplify",
    description="Code review for reuse, quality, and efficiency",
    when_to_use=(
        "After completing an implementation, review for code quality. "
        "Look for duplication, unnecessary complexity, and improvements."
    ),
    tags=["review", "simplify", "clean", "refactor", "quality"],
    prompt="""\
# Simplify & Review

Review recent code changes for quality and simplification opportunities.

## Checklist

1. **Duplication** — is any code repeated that could be shared?
2. **Complexity** — can any logic be simplified without losing clarity?
3. **Naming** — are names descriptive and consistent?
4. **Error handling** — are edge cases covered?
5. **Performance** — any obvious inefficiencies?
6. **Patterns** — does the code follow existing project patterns?

## Guidelines

- Focus on substantive issues, not style nitpicks
- Suggest specific improvements, not vague "could be better"
- Prioritize: correctness > clarity > performance > style
- Don't over-abstract — simpler is usually better
- Check if the project has a linter/formatter configured""",
)


# ── Initialize all bundled skills ───────────────────────────────────

def init_bundled_skills() -> None:
    """Register all bundled skills. Call once at startup."""
    for skill in (
        VERIFY_SKILL,
        DEBUG_SKILL,
        STUCK_SKILL,
        BATCH_SKILL,
        SIMPLIFY_SKILL,
    ):
        register_bundled_skill(skill)

    logger.debug("Registered %d bundled skills", len(_registry))


def get_bundled_skill_for_context(context: str) -> BundledSkillDefinition | None:
    """Find the best bundled skill for a given context string."""
    context_lower = context.lower()
    best: BundledSkillDefinition | None = None
    best_score = 0

    for skill in _registry.values():
        if not skill.enabled:
            continue
        score = 0
        for tag in skill.tags:
            if tag in context_lower:
                score += 10
        if skill.name in context_lower:
            score += 5
        if score > best_score:
            best_score = score
            best = skill

    return best if best_score > 0 else None


# Auto-initialize on import
init_bundled_skills()
