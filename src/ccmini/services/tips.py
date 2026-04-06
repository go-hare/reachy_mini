"""Tips Service — contextual tip display during loading/spinner.

Ported from Claude Code's ``services/tips/`` subsystem.

Shows helpful tips while the agent is working. Features:
- Per-tip cooldown (don't repeat too often)
- Context-aware relevance filtering (async ``is_relevant``)
- Tip history persistence (session count based)
- Custom user-defined tips (``~/.mini_agent/tips.json``)
- Least-recently-shown scheduling
- Tip categories for filtering
- Spinner tips override from config
- Enabled/disabled check from config
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..paths import mini_agent_home, mini_agent_path

logger = logging.getLogger(__name__)


# ── Tip category enum ────────────────────────────────────────────────

class TipCategory(str, Enum):
    WORKFLOW = "workflow"
    COMMANDS = "commands"
    FEATURES = "features"
    INTEGRATION = "integration"
    PERFORMANCE = "performance"
    ADVANCED = "advanced"


# ── Tip definition ───────────────────────────────────────────────────

@dataclass
class Tip:
    """A displayable tip with relevance and cooldown logic."""
    id: str
    content: str
    cooldown_sessions: int = 5
    is_relevant: Callable[..., bool | Awaitable[bool]] | None = None
    category: str = ""
    priority: int = 0


@dataclass
class TipContext:
    """Context for relevance filtering."""
    session_count: int = 0
    tools_used: set[str] = field(default_factory=set)
    files_touched: set[str] = field(default_factory=set)
    model: str = ""
    cwd: str = ""
    permission_mode: str = ""
    has_git: bool = False
    num_startups: int = 0
    bash_tools: set[str] = field(default_factory=set)


# ── Tip history ──────────────────────────────────────────────────────

_HISTORY_DIR = mini_agent_home()
_HISTORY_FILE = _HISTORY_DIR / "tip_history.json"


def _load_history() -> dict[str, int]:
    """Load tip history: {tip_id: last_shown_session_count}."""
    if not _HISTORY_FILE.exists():
        return {}
    try:
        data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        return {k: int(v) for k, v in data.items()}
    except Exception:
        return {}


def _save_history(history: dict[str, int]) -> None:
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _HISTORY_FILE.write_text(
            json.dumps(history, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("Failed to save tip history: %s", exc)


def record_tip_shown(tip_id: str, session_count: int) -> None:
    """Record that a tip was shown at the given session count."""
    history = _load_history()
    history[tip_id] = session_count
    _save_history(history)


def get_sessions_since_shown(tip_id: str, current_session: int) -> int:
    """Number of sessions since a tip was last shown. Infinity if never."""
    history = _load_history()
    last = history.get(tip_id)
    if last is None:
        return 999_999
    return current_session - last


# ── Tip registry ─────────────────────────────────────────────────────

_registry: list[Tip] = []
_custom_tips: list[Tip] = []


def register_tip(tip: Tip) -> None:
    _registry.append(tip)


def register_custom_tip(tip: Tip) -> None:
    _custom_tips.append(tip)


def get_all_tips() -> list[Tip]:
    return _registry + _custom_tips


def clear_tips() -> None:
    _registry.clear()
    _custom_tips.clear()


def get_tips_by_category(category: TipCategory | str) -> list[Tip]:
    """Return all tips matching a given category."""
    cat = category.value if isinstance(category, TipCategory) else category
    return [t for t in get_all_tips() if t.category == cat]


# ── Async relevance helper ───────────────────────────────────────────

async def _check_relevant(tip: Tip, context: TipContext | None) -> bool:
    """Call ``tip.is_relevant`` handling both sync and async callables."""
    if tip.is_relevant is None:
        return True
    try:
        result = tip.is_relevant(context) if context else tip.is_relevant(TipContext())
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            return bool(await result)
        return bool(result)
    except Exception:
        return False


# ── Built-in tips (~45 tips) ─────────────────────────────────────────

BUILTIN_TIPS: list[Tip] = [
    # ── COMMANDS ──────────────────────────────────────────────────
    Tip(
        id="compact-command",
        content="Use /compact to free up context when conversations get long.",
        cooldown_sessions=10,
        category=TipCategory.COMMANDS,
    ),
    Tip(
        id="memory-command",
        content="Use /memory to view and manage what the agent remembers across sessions.",
        cooldown_sessions=10,
        category=TipCategory.COMMANDS,
        is_relevant=lambda ctx: ctx.num_startups <= 5 if ctx else True,
    ),
    Tip(
        id="cost-command",
        content="Use /cost to check your token usage and estimated cost for this session.",
        cooldown_sessions=12,
        category=TipCategory.COMMANDS,
    ),
    Tip(
        id="context-command",
        content="Use /context to check how much of the context window this session has consumed.",
        cooldown_sessions=20,
        category=TipCategory.COMMANDS,
    ),
    Tip(
        id="theme-command",
        content="Use /theme <name> to switch the terminal color theme.",
        cooldown_sessions=20,
        category=TipCategory.COMMANDS,
    ),
    Tip(
        id="model-switch",
        content="Use /model to check which model the current session is using.",
        cooldown_sessions=15,
        category=TipCategory.COMMANDS,
    ),
    Tip(
        id="clear-command",
        content="Use /clear to clear the terminal before starting a new thread of work.",
        cooldown_sessions=15,
        category=TipCategory.COMMANDS,
        is_relevant=lambda ctx: ctx.num_startups > 10 if ctx else True,
    ),
    Tip(
        id="resume-session",
        content="Use --continue or --resume <id> to pick up a previous conversation.",
        cooldown_sessions=15,
        category=TipCategory.COMMANDS,
    ),
    Tip(
        id="buddy-command",
        content="Use /buddy to check on your companion, or /buddy hatch to create one.",
        cooldown_sessions=20,
        category=TipCategory.COMMANDS,
        is_relevant=lambda ctx: ctx.num_startups > 5 if ctx else True,
    ),
    Tip(
        id="theme-customize",
        content="Use /theme with no argument to see the available terminal themes.",
        cooldown_sessions=25,
        category=TipCategory.COMMANDS,
    ),
    Tip(
        id="rename-session",
        content="Name your conversations with /rename to find them easily when resuming later.",
        cooldown_sessions=15,
        category=TipCategory.COMMANDS,
        is_relevant=lambda ctx: ctx.num_startups > 10 if ctx else True,
    ),

    # ── WORKFLOW ──────────────────────────────────────────────────
    Tip(
        id="plan-mode",
        content="Use /plan to discuss approaches before coding — great for complex tasks.",
        cooldown_sessions=8,
        category=TipCategory.WORKFLOW,
    ),
    Tip(
        id="batch-work",
        content="For changes across many files, describe the pattern — the agent can batch similar edits.",
        cooldown_sessions=15,
        category=TipCategory.WORKFLOW,
    ),
    Tip(
        id="verify-changes",
        content="After code changes, the agent can verify them by running tests and checking for errors.",
        cooldown_sessions=10,
        category=TipCategory.WORKFLOW,
    ),
    Tip(
        id="be-specific",
        content="Specific instructions get better results. Instead of 'fix the bug', try 'the login form crashes when email is empty'.",
        cooldown_sessions=20,
        category=TipCategory.WORKFLOW,
    ),
    Tip(
        id="todo-list",
        content="Ask the agent to create a todo list when working on complex tasks to track progress.",
        cooldown_sessions=20,
        category=TipCategory.WORKFLOW,
    ),
    Tip(
        id="new-user-warmup",
        content="Start with small features or bug fixes, tell the agent to propose a plan, and verify suggested edits.",
        cooldown_sessions=3,
        category=TipCategory.WORKFLOW,
        is_relevant=lambda ctx: ctx.num_startups < 10 if ctx else True,
    ),
    Tip(
        id="prompt-queue",
        content="Hit Enter to queue up additional messages while the agent is working.",
        cooldown_sessions=5,
        category=TipCategory.WORKFLOW,
    ),
    Tip(
        id="web-search-tool",
        content="The default tool set now includes web_search, so the agent can look up current docs and recent information when needed.",
        cooldown_sessions=12,
        category=TipCategory.FEATURES,
    ),
    Tip(
        id="steer-realtime",
        content="Send messages to the agent while it works to steer it in real-time.",
        cooldown_sessions=20,
        category=TipCategory.WORKFLOW,
    ),
    Tip(
        id="session-resume",
        content="Sessions persist — use --continue or --resume to pick up where you left off.",
        cooldown_sessions=15,
        category=TipCategory.WORKFLOW,
    ),
    Tip(
        id="parallel-edits",
        content="The agent can edit multiple files in parallel for speed — just describe all the changes at once.",
        cooldown_sessions=15,
        category=TipCategory.WORKFLOW,
    ),

    # ── FEATURES ─────────────────────────────────────────────────
    Tip(
        id="image-paste",
        content="You can paste images directly into the prompt for analysis (Ctrl+V).",
        cooldown_sessions=20,
        category=TipCategory.FEATURES,
    ),
    Tip(
        id="drag-drop-images",
        content="Drag and drop image files into your terminal for the agent to analyze.",
        cooldown_sessions=15,
        category=TipCategory.FEATURES,
    ),
    Tip(
        id="keyboard-shortcuts",
        content="Use Shift+Enter for multi-line input. Run /terminal-setup to enable it.",
        cooldown_sessions=10,
        category=TipCategory.FEATURES,
    ),
    Tip(
        id="terminal-setup",
        content="Run /terminal-setup to enable convenient terminal integration like Shift+Enter for new lines.",
        cooldown_sessions=15,
        category=TipCategory.FEATURES,
    ),
    Tip(
        id="double-esc-rewind",
        content="Double-tap Esc to rewind the conversation to a previous point in time.",
        cooldown_sessions=15,
        category=TipCategory.FEATURES,
    ),
    Tip(
        id="custom-slash-commands",
        content="Create custom slash commands by adding .md files to .mini_agent/skills/ in your project.",
        cooldown_sessions=20,
        category=TipCategory.FEATURES,
        is_relevant=lambda ctx: ctx.num_startups > 10 if ctx else True,
    ),
    Tip(
        id="skills",
        content="Create .mini_agent/skills/ with SKILL.md files to teach the agent project-specific workflows.",
        cooldown_sessions=20,
        category=TipCategory.FEATURES,
    ),
    Tip(
        id="debug-skill",
        content="Stuck on a bug? The built-in 'debug' skill provides a systematic debugging workflow.",
        cooldown_sessions=10,
        category=TipCategory.FEATURES,
    ),
    Tip(
        id="status-line",
        content="Use /statusline to set up a custom status line beneath the input box.",
        cooldown_sessions=25,
        category=TipCategory.FEATURES,
    ),
    Tip(
        id="custom-agents",
        content="Use /plan before large changes to break work into steps and keep the session on track.",
        cooldown_sessions=15,
        category=TipCategory.FEATURES,
        is_relevant=lambda ctx: ctx.num_startups > 5 if ctx else True,
    ),

    # ── INTEGRATION ──────────────────────────────────────────────
    Tip(
        id="mcp-servers",
        content="Add MCP servers to extend the agent with databases, APIs, and external tools.",
        cooldown_sessions=25,
        category=TipCategory.INTEGRATION,
    ),
    Tip(
        id="http-server",
        content="Use mini-agent server to expose the agent over HTTP with session, query, tool, and tool-results endpoints.",
        cooldown_sessions=12,
        category=TipCategory.INTEGRATION,
    ),
    Tip(
        id="git-worktrees",
        content="Use git worktrees to run multiple agent sessions in parallel without conflicts.",
        cooldown_sessions=15,
        category=TipCategory.INTEGRATION,
        is_relevant=lambda ctx: ctx.has_git and ctx.num_startups > 20 if ctx else True,
    ),
    Tip(
        id="install-github-app",
        content="Use git worktrees plus named sessions to juggle multiple branches and conversations cleanly.",
        cooldown_sessions=15,
        category=TipCategory.INTEGRATION,
    ),

    # ── PERFORMANCE ──────────────────────────────────────────────
    Tip(
        id="context-window",
        content="If responses feel less accurate, your context might be full. Try /compact or start a new session.",
        cooldown_sessions=15,
        category=TipCategory.PERFORMANCE,
    ),
    Tip(
        id="effort-high",
        content="For complex architectural work, start with /plan so the agent can reason through tradeoffs before editing code.",
        cooldown_sessions=10,
        category=TipCategory.PERFORMANCE,
    ),

    # ── ADVANCED ─────────────────────────────────────────────────
    Tip(
        id="auto-mode",
        content="Auto mode lets the agent work without permission prompts — great for trusted repetitive tasks.",
        cooldown_sessions=15,
        category=TipCategory.ADVANCED,
        is_relevant=lambda ctx: ctx.permission_mode != "auto" if ctx else True,
    ),
    Tip(
        id="bash-mode",
        content="Use bash mode for complex multi-step shell operations that need interactive sequencing.",
        cooldown_sessions=15,
        category=TipCategory.ADVANCED,
    ),
    Tip(
        id="subagent-fanout",
        content="For big tasks, tell the agent to use subagents — they work in parallel and keep your main thread clean.",
        cooldown_sessions=10,
        category=TipCategory.ADVANCED,
    ),
    Tip(
        id="default-permission-config",
        content="Use /config to change your default permission mode (including Plan Mode).",
        cooldown_sessions=15,
        category=TipCategory.ADVANCED,
    ),
    Tip(
        id="color-multi-session",
        content="Running multiple sessions? Use /rename and mini-agent sessions to tell them apart at a glance.",
        cooldown_sessions=15,
        category=TipCategory.ADVANCED,
    ),
    Tip(
        id="agent-flag",
        content="Use /plan to scope a task first, then keep related work in separate named sessions when you need parallel threads.",
        cooldown_sessions=15,
        category=TipCategory.ADVANCED,
        is_relevant=lambda ctx: ctx.num_startups > 5 if ctx else True,
    ),
    Tip(
        id="important-prefix",
        content='Use "IMPORTANT:" prefix in CLAUDE.md rules to mark must-follow instructions.',
        cooldown_sessions=30,
        category=TipCategory.ADVANCED,
    ),
]


def init_builtin_tips() -> None:
    """Register all built-in tips. Call once at startup."""
    for tip in BUILTIN_TIPS:
        register_tip(tip)


# ── Custom tips from config ──────────────────────────────────────────

_CUSTOM_TIPS_FILE = mini_agent_path("tips.json")


def load_custom_tips() -> list[Tip]:
    """Load user-defined tips from ``~/.mini_agent/tips.json``.

    Expected format::

        [
            {"id": "custom-1", "text": "...", "category": "workflow"},
            {"id": "custom-2", "text": "...", "category": "commands", "cooldown": 5}
        ]
    """
    if not _CUSTOM_TIPS_FILE.exists():
        return []
    try:
        data = json.loads(_CUSTOM_TIPS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
    except Exception:
        logger.debug("Failed to load custom tips from %s", _CUSTOM_TIPS_FILE)
        return []

    tips: list[Tip] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        tip_id = entry.get("id", f"custom-{len(tips)}")
        text = entry.get("text", "")
        if not text:
            continue
        tips.append(Tip(
            id=tip_id,
            content=text,
            cooldown_sessions=int(entry.get("cooldown", 0)),
            category=entry.get("category", TipCategory.WORKFLOW),
        ))
    return tips


def init_custom_tips() -> None:
    """Load and register custom tips from the user's config."""
    for tip in load_custom_tips():
        register_custom_tip(tip)


# ── Spinner tips override ────────────────────────────────────────────

def get_spinner_tips_override() -> list[str] | None:
    """Return custom spinner tips from global config, or *None* for defaults.

    Config key: ``spinner_tips_override`` — a dict with::

        {"tips": ["tip text 1", "tip text 2"], "exclude_default": false}

    When ``exclude_default`` is true and tips are provided, *only* those
    tips are used during the spinner.
    """
    try:
        from ..config import _load_json, _global_config_path
        cfg = _load_json(_global_config_path())
    except Exception:
        return None

    override = cfg.get("spinner_tips_override")
    if not isinstance(override, dict):
        return None

    tips = override.get("tips")
    if not isinstance(tips, list) or not tips:
        return None

    return [str(t) for t in tips if isinstance(t, str) and t]


def get_spinner_override_tips_objects() -> list[Tip] | None:
    """Convert spinner override strings into Tip objects (no cooldown)."""
    texts = get_spinner_tips_override()
    if texts is None:
        return None
    return [
        Tip(id=f"spinner-override-{i}", content=t, cooldown_sessions=0)
        for i, t in enumerate(texts)
    ]


# ── Enabled check ────────────────────────────────────────────────────

def is_tips_enabled() -> bool:
    """Check config ``tips.enabled`` (default true)."""
    try:
        from ..config import _load_json, _global_config_path
        cfg = _load_json(_global_config_path())
    except Exception:
        return True

    tips_cfg = cfg.get("tips", {})
    if isinstance(tips_cfg, dict):
        return bool(tips_cfg.get("enabled", True))
    return True


def is_spinner_tips_enabled() -> bool:
    """Check config ``tips.spinner_tips_enabled`` (default true)."""
    try:
        from ..config import _load_json, _global_config_path
        cfg = _load_json(_global_config_path())
    except Exception:
        return True

    tips_cfg = cfg.get("tips", {})
    if isinstance(tips_cfg, dict):
        return bool(tips_cfg.get("spinner_tips_enabled", True))
    return True


# ── Tip scheduler ────────────────────────────────────────────────────


async def select_tip(
    context: TipContext | None = None,
    *,
    enabled: bool = True,
) -> Tip | None:
    """Select the best tip to show right now.

    Picks the tip that hasn't been shown for the longest time,
    filtering by relevance and cooldown.  Respects spinner overrides
    and the global enabled flag.
    """
    if not enabled or not is_tips_enabled():
        return None

    # If spinner override is configured with exclude_default, use those only
    try:
        from ..config import _load_json, _global_config_path
        cfg = _load_json(_global_config_path())
        override = cfg.get("spinner_tips_override", {})
        if isinstance(override, dict) and override.get("exclude_default"):
            override_tips = get_spinner_override_tips_objects()
            if override_tips:
                return random.choice(override_tips)
    except Exception:
        pass

    session = context.session_count if context else 0
    all_tips = get_all_tips()
    if not all_tips:
        return None

    available: list[tuple[int, Tip]] = []
    for tip in all_tips:
        elapsed = get_sessions_since_shown(tip.id, session)
        if elapsed < tip.cooldown_sessions:
            continue
        if not await _check_relevant(tip, context):
            continue
        available.append((elapsed, tip))

    # Merge spinner override tips (always eligible)
    override_tips = get_spinner_override_tips_objects()
    if override_tips:
        for tip in override_tips:
            available.append((999_999, tip))

    if not available:
        return None

    available.sort(key=lambda x: (-x[0], -x[1].priority))
    return available[0][1]


async def get_tip_text(context: TipContext | None = None) -> str | None:
    """Get tip text to display, or None if nothing to show."""
    if not is_spinner_tips_enabled():
        return None
    tip = await select_tip(context)
    if tip is None:
        return None
    session = context.session_count if context else 0
    record_tip_shown(tip.id, session)
    return tip.content


async def get_relevant_tips(context: TipContext | None = None) -> list[Tip]:
    """Return all currently relevant tips (used by the scheduler)."""
    session = context.session_count if context else 0
    results: list[Tip] = []
    for tip in get_all_tips():
        elapsed = get_sessions_since_shown(tip.id, session)
        if elapsed < tip.cooldown_sessions:
            continue
        if not await _check_relevant(tip, context):
            continue
        results.append(tip)
    return results


# Auto-init
init_builtin_tips()
init_custom_tips()
