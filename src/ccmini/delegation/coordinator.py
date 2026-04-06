"""Coordinator Mode — orchestrate workers instead of doing direct work.

Full port of Claude Code's ``coordinator/coordinatorMode.ts``. When active,
the main agent becomes a coordinator that:

- Directs workers (sub-agents) for research, implementation, verification
- Synthesizes worker results and communicates with the user
- Manages task concurrency (parallel reads, serial writes)
- Answers questions directly when no tools are needed
- Persists session mode for resume compatibility
- Provides worker agent definitions

The coordinator has a specialised system prompt plus runtime-injected
tool context describing the actual coordinator and worker tool pools.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _is_env_truthy_simple() -> bool:
    """Mirror ``isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE)`` (coordinatorMode.ts)."""
    v = os.environ.get("CLAUDE_CODE_SIMPLE", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _coordinator_worker_capabilities_blurb() -> str:
    """Match ``getCoordinatorSystemPrompt`` ``workerCapabilities`` in coordinatorMode.ts."""
    if _is_env_truthy_simple():
        return (
            "Workers have access to Bash, Read, and Edit tools, plus MCP tools "
            "from configured MCP servers."
        )
    return (
        "Workers have access to standard tools, MCP tools from configured MCP servers, "
        "and project skills via the Skill tool. Delegate skill invocations "
        "(e.g. /commit, /verify) to workers."
    )


def build_coordinator_context(
    coordinator_tools: list[str] | None = None,
) -> str:
    """Describe the coordinator's currently available host tools."""
    if coordinator_tools:
        tools_str = ", ".join(sorted(dict.fromkeys(coordinator_tools)))
        return (
            "You keep the full host tool pool in coordinator mode. "
            f"Your currently available tools are: {tools_str}"
        )
    return (
        "You keep the full host tool pool in coordinator mode. "
        "Use direct tools yourself when that is faster than delegating."
    )


# ---------------------------------------------------------------------------
# Coordinator system prompt — full port of coordinatorMode.ts (~370 lines)
# ---------------------------------------------------------------------------

COORDINATOR_SYSTEM_PROMPT = """\
You are Claude Code, an AI assistant that orchestrates software engineering tasks across \
multiple workers.

## 1. Your Role

You are a **coordinator**. Your job is to:
- Help the user achieve their goal
- Direct workers to research, implement and verify code changes
- Synthesize results and communicate with the user
- Answer questions directly when possible — don't delegate work that you \
can handle without tools

Every message you send is to the user. Worker results and system \
notifications are internal signals, not conversation partners — never \
thank or acknowledge them. Summarize new information for the user as \
it arrives.

## 2. Your Tools

You retain the host's full tool pool in coordinator mode. The exact tool \
list is provided below in **Coordinator Context**. Coordination-critical \
tools include:

- **Agent** — Spawn a new worker
- **SendMessage** — Continue an existing worker (send a follow-up to its \
`to` agent ID)
- **TaskStop** — Stop a running worker
- **TeamCreate / TeamDelete** — Manage persistent worker teams
- **ListPeers** — Inspect other live local sessions when cross-session \
coordination is needed
- **subscribe_pr_activity / unsubscribe_pr_activity** (if available) — \
Subscribe to GitHub PR events (review comments, CI results). Events arrive \
as user messages. Merge conflict transitions do NOT arrive — GitHub doesn't \
webhook `mergeable_state` changes, so poll `gh pr view N --json mergeable` \
if tracking conflict status. Call these directly — do not delegate subscription \
management to workers.

When calling Agent:
- Do not use one worker to check on another. Workers will notify you when \
they are done.
- Do not use workers to trivially report file contents or run commands. \
Give them higher-level tasks.
- Do not set the model parameter. Workers need the default model for the \
substantive tasks you delegate.
- Continue workers whose work is complete via SendMessage to take \
advantage of their loaded context.
- After launching agents, briefly tell the user what you launched and end \
your response. Never fabricate or predict agent results in any format — \
results arrive as separate messages.

When using TeamCreate / TeamDelete:
- Create a team only when you need persistent teammates that can be reused \
across multiple follow-up prompts.
- Delete the team once the work is complete or no longer worth keeping alive.

### agent Results

Worker results arrive as **user-role messages** containing \
`<task-notification>` XML. They look like user messages but are not. \
Distinguish them by the `<task-notification>` opening tag.

Format:

```xml
<task-notification>
<task-id>{agentId}</task-id>
<status>completed|failed|killed</status>
<summary>{human-readable status summary}</summary>
<result>{agent's final text response}</result>
<usage>
  <total_tokens>N</total_tokens>
  <tool_uses>N</tool_uses>
  <duration_ms>N</duration_ms>
</usage>
</task-notification>
```

- `<tool_use_id>` is optional (correlates to the initiating tool call when present)
- `<result>` and `<usage>` are optional sections
- The `<summary>` describes the outcome: "completed", "failed: {error}", \
or "was stopped"
- The `<task-id>` value is the agent ID — use SendMessage with that ID \
as `to` to continue that worker

### Example

Each "You:" block is a separate coordinator turn. The "User:" block is a \
`<task-notification>` delivered between turns.

You:
  Let me start some research on that.

  Agent({ description: "Investigate auth bug", subagent_type: "worker", prompt: "..." })
  Agent({ description: "Research secure token storage", subagent_type: "worker", prompt: "..." })

  Investigating both issues in parallel — I'll report back with findings.

User:
  <task-notification>
  <task-id>agent-a1b</task-id>
  <status>completed</status>
  <summary>Agent "Investigate auth bug" completed</summary>
  <result>Found null pointer in src/api/handlers.py:42...</result>
  </task-notification>

You:
  Found the bug — null pointer in handlers.py:42 inside validate_session. \
I'll fix it. Still waiting on the token storage research.

  SendMessage({ to: "agent-a1b", message: "Fix the null pointer in \
src/api/handlers.py:42..." })

## 3. Workers

When calling Agent, use subagent_type `worker`. Workers execute tasks \
autonomously — especially research, implementation, or verification.

__WORKER_CAPABILITIES__

## 4. Task Workflow

Most tasks can be broken down into the following phases:

### Phases

| Phase | Who | Purpose |
|-------|-----|---------|
| Research | Workers (parallel) | Investigate codebase, find files, understand problem |
| Synthesis | **You** (coordinator) | Read findings, understand the problem, craft implementation specs (see Section 5) |
| Implementation | Workers | Make targeted changes per spec, commit |
| Verification | Workers | Test changes work |

### Concurrency

**Parallelism is your superpower. Workers are async. Launch independent \
workers concurrently whenever possible — don't serialize work that can \
run simultaneously and look for opportunities to fan out. When doing \
research, cover multiple angles. To launch workers in parallel, make \
multiple tool calls in a single message.**

Manage concurrency:
- **Read-only tasks** (research) — run in parallel freely
- **Write-heavy tasks** (implementation) — one at a time per set of files
- **Verification** can sometimes run alongside implementation on different \
file areas

### What Real Verification Looks Like

Verification means **proving the code works**, not confirming it exists. \
A verifier that rubber-stamps weak work undermines everything.

- Run tests **with the feature enabled** — not just "tests pass"
- Run typechecks and **investigate errors** — don't dismiss as "unrelated"
- Be skeptical — if something looks off, dig in
- **Test independently** — prove the change works, don't rubber-stamp

### Handling Worker Failures

When a worker reports failure (tests failed, build errors, file not found):
- Continue the same worker with SendMessage — it has the full error context
- If a correction attempt fails, try a different approach or report to user

### Stopping Workers

Use TaskStop to stop a worker you sent in the wrong direction — for \
example, when you realize mid-flight that the approach is wrong, or the \
user changes requirements after you launched the worker. Pass the `task_id` \
from the Agent tool's launch result. Stopped workers can be continued \
with SendMessage.

```
// Launched a worker to refactor auth to use JWT
Agent({ description: "Refactor auth to JWT", subagent_type: "worker", prompt: "Replace session-based auth with JWT..." })
// ... returns task_id: "agent-x7q" ...

// User clarifies: "Actually, keep sessions — just fix the null pointer"
TaskStop({ task_id: "agent-x7q" })

// Continue with corrected instructions
SendMessage({ to: "agent-x7q", message: "Stop the JWT refactor. Instead, fix the null pointer in src/api/handlers.py:42..." })
```

## 5. Writing Worker Prompts

**Workers can't see your conversation.** Every prompt must be self-contained \
with everything the worker needs. After research completes, you always do \
two things: (1) synthesize findings into a specific prompt, and (2) choose \
whether to continue that worker via SendMessage or spawn a fresh one.

### Always synthesize — your most important job

When workers report research findings, **you must understand them before \
directing follow-up work**. Read the findings. Identify the approach. \
Then write a prompt that proves you understood by including specific file \
paths, line numbers, and exactly what to change.

Never write "based on your findings" or "based on the research." These \
phrases delegate understanding to the worker instead of doing it yourself. \
You never hand off understanding to another worker.

```
// Anti-pattern — lazy delegation (bad whether continuing or spawning)
Agent({ prompt: "Based on your findings, fix the auth bug" })
Agent({ prompt: "The worker found an issue in the auth module. Please fix it." })

// Good — synthesized spec (works with either continue or spawn)
Agent({ prompt: "Fix the null pointer in src/api/handlers.py:42. The user \
field on Session (src/models/session.py:15) is undefined when sessions expire but \
the token remains cached. Add a null check before user.id access — if null, \
return 401 with 'Session expired'. Commit and report the hash." })
```

A well-synthesized spec gives the worker everything it needs in a few \
sentences. It does not matter whether the worker is fresh or continued — \
the spec quality determines the outcome.

### Add a purpose statement

Include a brief purpose so workers can calibrate depth and emphasis:

- "This research will inform a PR description — focus on user-facing changes."
- "I need this to plan an implementation — report file paths, line numbers, \
and type signatures."
- "This is a quick check before we merge — just verify the happy path."

### Choose continue vs. spawn by context overlap

After synthesizing, decide whether the worker's existing context helps or \
hurts:

| Situation | Mechanism | Why |
|-----------|-----------|-----|
| Research explored exactly the files that need editing | **Continue** (SendMessage) with synthesized spec | Worker already has the files in context AND now gets a clear plan |
| Research was broad but implementation is narrow | **Spawn fresh** (agent) with synthesized spec | Avoid dragging along exploration noise; focused context is cleaner |
| Correcting a failure or extending recent work | **Continue** | Worker has the error context and knows what it just tried |
| Verifying code a different worker just wrote | **Spawn fresh** | Verifier should see the code with fresh eyes, not carry implementation assumptions |
| First implementation attempt used the wrong approach entirely | **Spawn fresh** | Wrong-approach context pollutes the retry; clean slate avoids anchoring on the failed path |
| Completely unrelated task | **Spawn fresh** | No useful context to reuse |

There is no universal default. Think about how much of the worker's context \
overlaps with the next task. High overlap -> continue. Low overlap -> spawn.

### Continue mechanics

When continuing a worker with SendMessage, it has full context from its \
previous run:
```
// Continuation — worker finished research, now give it a synthesized spec
SendMessage({ to: "xyz-456", message: "Fix the null pointer in \
src/api/handlers.py:42. The user field is undefined when Session.expired \
is true but the token is still cached. Add a null check before accessing \
user.id — if null, return 401 with 'Session expired'. Commit and report \
the hash." })
```

```
// Correction — worker just reported test failures, keep it brief
SendMessage({ to: "xyz-456", message: "Two tests still failing at lines \
58 and 72 — update the assertions to match the new error message." })
```

### Prompt tips

**Good examples:**

1. Implementation: "Fix the null pointer in src/api/handlers.py:42. The \
user field can be undefined when the session expires. Add a null check and \
return early with an appropriate error. Commit and report the hash."

2. Precise git operation: "Create a new branch from main called \
'fix/session-expiry'. Cherry-pick only commit abc123 onto it. Push and \
create a draft PR targeting main. Report the PR URL."

3. Correction (continued worker, short): "The tests failed on the null \
check you added — tests/test_handlers.py:58 expects 'Invalid session' but you \
changed it to 'Session expired'. Fix the assertion."

**Bad examples:**

1. "Fix the bug we discussed" — no context, workers can't see your conversation
2. "Based on your findings, implement the fix" — lazy delegation
3. "Create a PR for the recent changes" — ambiguous scope
4. "Something went wrong with the tests, can you look?" — no error, no path

Additional tips:
- Include file paths, line numbers, error messages — workers start fresh \
and need complete context
- State what "done" looks like
- For implementation: "Run relevant tests and typecheck, then commit your \
changes and report the hash"
- For research: "Report findings — do not modify files"
- Be precise about git operations — specify branch names, commit hashes
- When continuing for corrections: reference what the worker did, not what \
you discussed with the user
- For verification: "Prove the code works, don't just confirm it exists"
- For verification: "Try edge cases and error paths"
- For verification: "Investigate failures — don't dismiss as unrelated"

## 6. Example Session

User: "There's a null pointer in the auth module. Can you fix it?"

You:
  Let me investigate first.

  Agent({ description: "Investigate auth bug", subagent_type: "worker", prompt: "Investigate the \
auth module in src/api/. Find where null pointer exceptions could occur \
around session handling and token validation... Report specific file paths, \
line numbers, and types involved. Do not modify files." })
  Agent({ description: "Research auth tests", subagent_type: "worker", prompt: "Find all test files \
related to src/api/. Report the test structure, what's covered, and any \
gaps around session expiry... Do not modify files." })

  Investigating from two angles — I'll report back with findings.

User:
  <task-notification>
  <task-id>agent-a1b</task-id>
  <status>completed</status>
  <summary>Agent "Investigate auth bug" completed</summary>
  <result>Found null pointer in src/api/handlers.py:42. The user field \
on Session is undefined when the session expires but ...</result>
  </task-notification>

You:
  Found the bug — null pointer in handlers.py:42.

  SendMessage({ to: "agent-a1b", message: "Fix the null pointer in \
src/api/handlers.py:42. Add a null check before accessing user.id — \
if null, ... Commit and report the hash." })

  Fix is in progress.

User:
  How's it going?

You:
  Fix is in progress. Still waiting to hear back about the test suite.\
"""


def get_coordinator_system_prompt() -> str:
    """Return the full coordinator system prompt."""
    return COORDINATOR_SYSTEM_PROMPT.replace(
        "__WORKER_CAPABILITIES__",
        _coordinator_worker_capabilities_blurb(),
    )


# ---------------------------------------------------------------------------
# Coordinator tool set
# ---------------------------------------------------------------------------

COORDINATOR_ALLOWED_TOOLS = frozenset({
    "Agent",
    "SendMessage",
    "TaskStop",
    "TeamCreate",
    "TeamDelete",
    "ListPeers",
    "SyntheticOutput",
})

WORKER_DISALLOWED_TOOLS = frozenset({
    "Agent",
    "SendMessage",
    "TaskStop",
    "TodoWrite",
    "AskUserQuestion",
    "TeamCreate",
    "TeamDelete",
    "EnterPlanMode",
    "ExitPlanMode",
    "VerifyPlanExecution",
    "ListPeers",
    "Sleep",
})


def get_coordinator_tool_names() -> set[str]:
    """Return the set of tool names available in coordinator mode."""
    return set(COORDINATOR_ALLOWED_TOOLS)


def get_worker_disallowed_tools() -> set[str]:
    """Tools that workers must NOT have access to."""
    return set(WORKER_DISALLOWED_TOOLS)


# ---------------------------------------------------------------------------
# Worker context — aligned with coordinatorMode.ts getCoordinatorUserContext()
# ---------------------------------------------------------------------------

# ``INTERNAL_WORKER_TOOLS`` in coordinatorMode.ts — excluded from the listed set.
_INTERNAL_WORKER_CONTEXT_EXCLUSIONS = frozenset({
    "TeamCreate",
    "TeamDelete",
    "SendMessage",
    "SyntheticOutput",
})

# Mirrors ``src/constants/tools.ts`` ``ASYNC_AGENT_ALLOWED_TOOLS`` (wire names).
_ASYNC_AGENT_ALLOWED_TOOLS = frozenset({
    "Read",
    "WebSearch",
    "TodoWrite",
    "Grep",
    "WebFetch",
    "Glob",
    "Bash",
    "PowerShell",
    "Edit",
    "Write",
    "NotebookEdit",
    "Skill",
    "ToolSearch",
    "EnterWorktree",
    "ExitWorktree",
})


def coordinator_worker_tool_names_for_context() -> list[str]:
    """Sorted tool names shown in ``workerToolsContext`` (workers spawned via Agent)."""
    if _is_env_truthy_simple():
        return sorted(["Bash", "Read", "Edit"])
    allowed = _ASYNC_AGENT_ALLOWED_TOOLS - _INTERNAL_WORKER_CONTEXT_EXCLUSIONS
    return sorted(allowed)


def scratchpad_gate_for_worker_context() -> bool:
    """When False, omit scratchpad lines even if a scratchpad path is set.

    Reference uses GrowthBook ``tengu_scratch``; mini_agent uses env:
    ``MINI_AGENT_COORDINATOR_SCRATCHPAD_CONTEXT`` — default **on** (legacy).
    Set to ``0`` / ``false`` to mirror a disabled scratchpad gate.
    """
    v = os.environ.get("MINI_AGENT_COORDINATOR_SCRATCHPAD_CONTEXT", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return True


def build_worker_context(
    *,
    worker_tools: list[str] | None = None,
    mcp_server_names: list[str] | None = None,
    scratchpad_dir: str | None = None,
) -> str:
    """Build the context section injected into the coordinator's prompt.

    Tells the coordinator which tools its workers have access to.
    """
    if worker_tools:
        tools_str = ", ".join(sorted(worker_tools))
    else:
        tools_str = ", ".join(coordinator_worker_tool_names_for_context())

    # AGENT_TOOL_NAME in reference is ``Agent`` (see coordinatorMode.ts).
    content = (
        f"Workers spawned via the Agent tool have access to these tools: {tools_str}"
    )

    if mcp_server_names:
        servers = ", ".join(mcp_server_names)
        content += (
            f"\n\nWorkers also have access to MCP tools from "
            f"connected MCP servers: {servers}"
        )

    if scratchpad_dir and scratchpad_gate_for_worker_context():
        content += (
            f"\n\nScratchpad directory: {scratchpad_dir}\n"
            "Workers can read and write here without permission prompts. "
            "Use this for durable cross-worker knowledge — structure files "
            "however fits the work."
        )

    return content


def get_coordinator_user_context(
    mcp_clients: list[dict[str, Any]] | list[Any],
    scratchpad_dir: str | None = None,
    *,
    coordinator_tools: list[str] | None = None,
    worker_tools: list[str] | None = None,
) -> dict[str, str]:
    """Mirror coordinatorMode.ts getCoordinatorUserContext()."""
    if not is_coordinator_mode():
        return {}

    server_names: list[str] = []
    for client in mcp_clients:
        if isinstance(client, dict):
            name = str(client.get("name", "")).strip()
        else:
            name = str(getattr(client, "name", "")).strip()
        if name:
            server_names.append(name)

    return {
        "coordinatorToolsContext": build_coordinator_context(coordinator_tools),
        "workerToolsContext": build_worker_context(
            worker_tools=worker_tools or coordinator_worker_tool_names_for_context(),
            mcp_server_names=server_names or None,
            scratchpad_dir=scratchpad_dir,
        )
    }


# ---------------------------------------------------------------------------
# Env-var-based detection (mirrors isCoordinatorMode())
# ---------------------------------------------------------------------------

def coordinator_mode_feature_enabled() -> bool:
    """Mirror bundle ``feature('COORDINATOR_MODE')`` from ``coordinatorMode.ts``.

    There is no ``bun:bundle`` in mini_agent; use ``MINI_AGENT_COORDINATOR_MODE_FEATURE``.
    Default **on** (``1``): when ``0`` / ``false`` / ``no`` / ``off``, coordinator
    mode is never active, matching a disabled product feature gate.
    """
    raw = os.environ.get("MINI_AGENT_COORDINATOR_MODE_FEATURE", "1")
    v = raw.strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True


def is_coordinator_mode() -> bool:
    """Coordinator mode when the feature gate is on *and* env is truthy.

    Matches ``isCoordinatorMode()`` in ``coordinatorMode.ts``: ``feature(...)``
    then ``isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE)``.
    """
    if not coordinator_mode_feature_enabled():
        return False
    val = os.environ.get("CLAUDE_CODE_COORDINATOR_MODE", "").lower()
    return val in ("1", "true", "yes", "on")


def set_coordinator_mode_env(active: bool) -> None:
    """Set the coordinator mode env var."""
    if active:
        os.environ["CLAUDE_CODE_COORDINATOR_MODE"] = "1"
    else:
        os.environ.pop("CLAUDE_CODE_COORDINATOR_MODE", None)


# ---------------------------------------------------------------------------
# Session mode persistence (mirrors matchSessionMode())
# ---------------------------------------------------------------------------

SessionMode = str  # "coordinator" | "normal" | None

_MODE_FILE = ".ccmini/session_mode.json"
_LEGACY_MODE_FILE = ".mini_agent/session_mode.json"


def _mode_paths(cwd: str | Path | None = None) -> tuple[Path, Path]:
    root = Path(cwd) if cwd else Path.cwd()
    return root / _MODE_FILE, root / _LEGACY_MODE_FILE


def save_session_mode(mode: SessionMode, cwd: str | Path | None = None) -> None:
    """Persist the current mode so future resumes know what mode was used."""
    path, legacy_path = _mode_paths(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    if legacy_path.exists():
        try:
            legacy_path.unlink()
            legacy_parent = legacy_path.parent
            if legacy_parent.exists() and not any(legacy_parent.iterdir()):
                legacy_parent.rmdir()
        except OSError:
            pass


def load_session_mode(cwd: str | Path | None = None) -> SessionMode | None:
    """Read the persisted session mode."""
    path, legacy_path = _mode_paths(cwd)
    for candidate in (path, legacy_path):
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            mode = data.get("mode")
        except (json.JSONDecodeError, OSError):
            continue
        if candidate == legacy_path:
            try:
                save_session_mode(mode, cwd)
            except OSError:
                pass
        return mode
    return None


def match_session_mode(
    session_mode: SessionMode | None,
) -> str | None:
    """Flip the env var to match a resumed session's mode.

    Returns a warning message if the mode was switched, or None.
    Mirrors Claude Code's coordinatorMode.ts matchSessionMode().
    """
    if not session_mode:
        return None

    current = is_coordinator_mode()
    session_is_coordinator = session_mode == "coordinator"

    if current == session_is_coordinator:
        return None

    set_coordinator_mode_env(session_is_coordinator)
    if session_is_coordinator:
        return "Entered coordinator mode to match resumed session."
    return "Exited coordinator mode to match resumed session."


# ---------------------------------------------------------------------------
# Worker agent definitions (mirrors getCoordinatorAgents())
# ---------------------------------------------------------------------------

class WorkerAgentDef:
    """Definition of a worker agent type available in coordinator mode."""

    def __init__(
        self,
        name: str,
        description: str,
        *,
        tools: list[str] | None = None,
        system_prompt_addendum: str = "",
        background: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.tools = tools
        self.system_prompt_addendum = system_prompt_addendum
        self.background = background

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tools": self.tools,
            "system_prompt_addendum": self.system_prompt_addendum,
            "background": self.background,
        }


# Default worker types for coordinator mode
WORKER_AGENT = WorkerAgentDef(
    name="worker",
    description="General-purpose worker for research, implementation, and verification tasks",
)

RESEARCH_WORKER = WorkerAgentDef(
    name="researcher",
    description="Read-only worker for codebase investigation and analysis",
    system_prompt_addendum="You are a research worker. Do NOT modify any files. "
    "Report findings with specific file paths, line numbers, and code snippets.",
)

VERIFICATION_WORKER = WorkerAgentDef(
    name="verifier",
    description="Worker focused on testing and verification of changes",
    system_prompt_addendum="You are a verification worker. Your job is to PROVE "
    "the code works, not just confirm it exists. Run tests, check edge cases, "
    "and investigate failures thoroughly.",
)

_DEFAULT_COORDINATOR_AGENTS = [
    WORKER_AGENT,
    RESEARCH_WORKER,
    VERIFICATION_WORKER,
]


def get_coordinator_agents() -> list[WorkerAgentDef]:
    """Return the list of worker agent types available in coordinator mode."""
    return list(_DEFAULT_COORDINATOR_AGENTS)


def register_coordinator_agent(agent_def: WorkerAgentDef) -> None:
    """Register an additional worker agent type."""
    _DEFAULT_COORDINATOR_AGENTS.append(agent_def)


# ---------------------------------------------------------------------------
# CoordinatorMode class
# ---------------------------------------------------------------------------

class CoordinatorMode:
    """Manages coordinator mode state and configuration.

    Usage::

        coord = CoordinatorMode()
        coord.activate()

        if coord.is_active:
            prompt = coord.get_system_prompt()
            allowed = coord.filter_tools(all_tools)
    """

    def __init__(self) -> None:
        self._active = False
        self._coordinator_tool_names: list[str] = []
        self._worker_tool_names: list[str] = []
        self._mcp_server_names: list[str] = []
        self._scratchpad_dir: str | None = None

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(
        self,
        *,
        coordinator_tool_names: list[str] | None = None,
        worker_tool_names: list[str] | None = None,
        mcp_server_names: list[str] | None = None,
        scratchpad_dir: str | None = None,
    ) -> None:
        self._active = True
        self._coordinator_tool_names = list(coordinator_tool_names or [])
        self._worker_tool_names = list(worker_tool_names or [])
        self._mcp_server_names = list(mcp_server_names or [])
        self._scratchpad_dir = scratchpad_dir
        set_coordinator_mode_env(True)
        logger.info("Coordinator mode activated")

    def deactivate(self) -> None:
        self._active = False
        set_coordinator_mode_env(False)
        logger.info("Coordinator mode deactivated")

    def get_system_prompt(self) -> str:
        """Return the full system prompt for coordinator mode."""
        parts = [get_coordinator_system_prompt()]
        coord_ctx = build_coordinator_context(self._coordinator_tool_names or None)
        parts.append(f"\n## Coordinator Context\n\n{coord_ctx}")
        ctx = build_worker_context(
            worker_tools=self._worker_tool_names or None,
            mcp_server_names=self._mcp_server_names or None,
            scratchpad_dir=self._scratchpad_dir,
        )
        parts.append(f"\n## Worker Context\n\n{ctx}")
        return "\n".join(parts)

    def get_worker_agents(self) -> list[WorkerAgentDef]:
        """Return available worker agent types."""
        return get_coordinator_agents()

    def filter_tools(self, tools: list[Any]) -> list[Any]:
        """Coordinator keeps the full host tool pool."""
        return list(tools)

    def filter_worker_tools(self, tools: list[Any]) -> list[Any]:
        """Filter a tool list to remove coordinator-only tools."""
        disallowed = get_worker_disallowed_tools()
        return [t for t in tools if getattr(t, "name", "") not in disallowed]

    def save_mode(self, cwd: str | Path | None = None) -> None:
        """Persist current mode for session resume."""
        mode = "coordinator" if self._active else "normal"
        save_session_mode(mode, cwd)

    def restore_mode(self, cwd: str | Path | None = None) -> str | None:
        """Restore mode from a previous session. Returns warning if switched."""
        stored = load_session_mode(cwd)
        return match_session_mode(stored)
