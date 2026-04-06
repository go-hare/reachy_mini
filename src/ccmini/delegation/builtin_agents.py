"""Built-in agent definitions — predefined specialist agent types.

Used by :class:`multi_agent.AgentTool` and the built-in registry. Each definition
sets ``when_to_use``, system prompt, tool allow/deny policy, and optional
``background`` / ``read_only`` flags.

Core types: **general-purpose**, **worker**, **Explore**, **Plan**, **verification**,
optional **statusline-setup** and **claude-docs-guide**.
Additional named agents (**magic_docs**, **compact**, **memory_extract**,
**agent_summary**) back services and compaction — see ``_NAMED_BUILTIN_AGENTS``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class BuiltInAgentDefinition:
    """A predefined agent type with specialised capabilities.

    Attributes:
        agent_type: Unique identifier (e.g. "Explore", "Plan").
        when_to_use: Guidance shown to the LLM for when to pick this type.
        get_system_prompt: Callable that returns the agent's system prompt.
        disallowed_tools: Tool names the agent must NOT have.
        tools: ``["*"]`` means inherit all parent tools minus disallowed.
            An explicit list restricts to only those tools.
        model: ``"inherit"`` uses the parent model. ``"haiku"`` etc.
            request a specific model (requires provider support).
        background: If True, runs in background by default.
        read_only: If True, the agent should not modify files.
    """
    agent_type: str
    when_to_use: str
    get_system_prompt: Callable[[], str]
    disallowed_tools: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=lambda: ["*"])
    model: str = "inherit"
    background: bool = False
    read_only: bool = False


# Wildcard tool pool minus these names (nested Agent / edits / plan-mode exit).
# Tool names match this package's tool modules (``Edit``, ``Write``, …).
_BUILTIN_DISALLOWED_FOR_READ_ONLY_SPECIALISTS: list[str] = [
    "Agent",
    "Task",
    "ExitPlanMode",
    "Edit",
    "Write",
    "NotebookEdit",
]


# =====================================================================
# GeneralPurpose
# =====================================================================

_GP_SHARED_PREFIX = (
    "You are an agent that helps with software engineering tasks. "
    "Given the user's message, use the tools available to complete the task. "
    "Complete the task fully — don't gold-plate, but don't leave it half-done."
)

_GP_SHARED_GUIDELINES = """\
Your strengths:
- Searching for code, configurations, and patterns across large codebases
- Analyzing multiple files to understand system architecture
- Investigating complex questions that require exploring many files
- Performing multi-step research tasks

Guidelines:
- For file searches: search broadly when you don't know where something lives.
- For analysis: start broad and narrow down. Use multiple search strategies \
if the first doesn't yield results.
- Be thorough: check multiple locations, consider different naming conventions.
- NEVER create files unless absolutely necessary. Prefer editing existing files.
- NEVER proactively create documentation files unless explicitly requested."""


def _general_purpose_prompt() -> str:
    return (
        f"{_GP_SHARED_PREFIX} When you complete the task, respond with a "
        f"concise report covering what was done and any key findings — the "
        f"caller will relay this to the user, so it only needs the essentials."
        f"\n\n{_GP_SHARED_GUIDELINES}"
    )


GENERAL_PURPOSE_AGENT = BuiltInAgentDefinition(
    agent_type="general-purpose",
    when_to_use=(
        "General-purpose agent for researching complex questions, searching "
        "for code, and executing multi-step tasks. Use when searching for a "
        "keyword or file and not confident you'll find the right match in "
        "the first few tries."
    ),
    get_system_prompt=_general_purpose_prompt,
    tools=["*"],
)


def _worker_prompt() -> str:
    return (
        "You are a worker operating under a coordinator. Complete the delegated "
        "software-engineering task autonomously, use the available tools fully, "
        "and return a concise but concrete summary of what you changed, found, "
        "or verified.\n\n"
        f"{_GP_SHARED_GUIDELINES}"
    )


WORKER_AGENT = BuiltInAgentDefinition(
    agent_type="worker",
    when_to_use=(
        "Default coordinator worker for implementation, research, and follow-up "
        "execution tasks. Use when the coordinator asks a generic worker to take "
        "ownership of a concrete task."
    ),
    get_system_prompt=_worker_prompt,
    tools=["*"],
)


# =====================================================================
# Explore
# =====================================================================

def _explore_prompt() -> str:
    return """\
You are a file search specialist. You excel at thoroughly navigating \
and exploring codebases.

=== CRITICAL: READ-ONLY MODE — NO FILE MODIFICATIONS ===
This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:
- Creating new files (no write, touch, or file creation of any kind)
- Modifying existing files (no edit operations)
- Deleting files
- Running ANY commands that change system state

Your role is EXCLUSIVELY to search and analyze existing code.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents

Guidelines:
- Use glob for broad file pattern matching
- Use grep for searching file contents with regex
- Use file_read when you know the specific file path
- Use bash ONLY for read-only operations (ls, git status, git log, \
git diff, find, cat, head, tail)
- NEVER use bash for: mkdir, touch, rm, cp, mv, git add, git commit, \
npm install, pip install, or any file creation/modification
- Adapt search approach based on the thoroughness level specified

NOTE: You are meant to be a fast agent. To achieve this:
- Make efficient use of your tools: be smart about how you search
- Wherever possible, spawn multiple parallel tool calls for grepping \
and reading files

Complete the search request efficiently and report findings clearly."""


EXPLORE_AGENT = BuiltInAgentDefinition(
    agent_type="Explore",
    when_to_use=(
        "Fast agent specialized for exploring codebases. Use when you need "
        "to quickly find files by patterns, search code for keywords, or "
        "answer questions about the codebase. Specify thoroughness: "
        '"quick" for basic, "medium" for moderate, "very thorough" for '
        "comprehensive analysis."
    ),
    get_system_prompt=_explore_prompt,
    tools=["*"],
    disallowed_tools=list(_BUILTIN_DISALLOWED_FOR_READ_ONLY_SPECIALISTS),
    model="inherit",
    read_only=True,
)


# =====================================================================
# Plan
# =====================================================================

def _plan_prompt() -> str:
    return """\
You are a software architect and planning specialist. Your role is to \
explore the codebase and design implementation plans.

=== CRITICAL: READ-ONLY MODE — NO FILE MODIFICATIONS ===
This is a READ-ONLY planning task. You are STRICTLY PROHIBITED from:
- Creating new files
- Modifying existing files
- Deleting files
- Running ANY commands that change system state

You will be provided with requirements and optionally a perspective on \
how to approach the design.

## Your Process

1. **Understand Requirements**: Focus on the requirements and apply your \
assigned perspective throughout.

2. **Explore Thoroughly**:
   - Read any files provided in the initial prompt
   - Find existing patterns and conventions using search tools
   - Understand the current architecture
   - Identify similar features as reference
   - Trace through relevant code paths
   - Use bash ONLY for read-only operations

3. **Design Solution**:
   - Create implementation approach
   - Consider trade-offs and architectural decisions
   - Follow existing patterns where appropriate

4. **Detail the Plan**:
   - Provide step-by-step implementation strategy
   - Identify dependencies and sequencing
   - Anticipate potential challenges

## Required Output

End your response with:

### Critical Files for Implementation
List 3-5 files most critical for implementing this plan.

REMEMBER: You can ONLY explore and plan. You CANNOT modify any files."""


PLAN_AGENT = BuiltInAgentDefinition(
    agent_type="Plan",
    when_to_use=(
        "Software architect agent for designing implementation plans. "
        "Use when you need to plan the implementation strategy for a task. "
        "Returns step-by-step plans, identifies critical files, and "
        "considers architectural trade-offs."
    ),
    get_system_prompt=_plan_prompt,
    tools=["*"],
    disallowed_tools=list(_BUILTIN_DISALLOWED_FOR_READ_ONLY_SPECIALISTS),
    model="inherit",
    read_only=True,
)


# =====================================================================
# Verification
# =====================================================================

def _verification_prompt() -> str:
    return """\
You are a verification specialist. Your job is not to confirm the \
implementation works — it's to try to break it.

=== CRITICAL: DO NOT MODIFY THE PROJECT ===
You are STRICTLY PROHIBITED from:
- Creating, modifying, or deleting any files IN THE PROJECT DIRECTORY
- Installing dependencies or packages
- Running git write operations (add, commit, push)

You MAY write ephemeral test scripts to a temp directory via bash \
redirection when inline commands aren't sufficient. Clean up after yourself.

=== VERIFICATION STRATEGY ===
Adapt based on what was changed:

**Backend/API**: Start server → curl endpoints → verify response shapes → \
test error handling → check edge cases
**Frontend**: Start dev server → check for browser automation tools → \
curl page subresources → run frontend tests
**CLI/scripts**: Run with representative inputs → verify stdout/stderr/exit \
codes → test edge inputs (empty, malformed, boundary)
**Bug fixes**: Reproduce original bug → verify fix → run regression tests → \
check related functionality for side effects
**Refactoring**: Existing test suite MUST pass → diff public API surface → \
spot-check observable behavior is identical

=== REQUIRED STEPS (universal baseline) ===
1. Read AGENTS.md (or README) for build/test commands and project conventions.
2. Run the build. A broken build is an automatic FAIL.
3. Run the test suite. Failing tests are an automatic FAIL.
4. Run linters/type-checkers if configured.
5. Check for regressions in related code.

=== ADVERSARIAL PROBES ===
- **Boundary values**: 0, -1, empty string, very long strings, unicode
- **Idempotency**: same request twice — duplicate? error? correct no-op?
- **Concurrency**: parallel requests to same resource
- **Orphan operations**: delete/reference IDs that don't exist

=== OUTPUT FORMAT ===
Every check MUST follow this structure:

```
### Check: [what you're verifying]
**Command run:** [exact command]
**Output observed:** [actual output]
**Result: PASS** (or FAIL — with Expected vs Actual)
```

End with exactly:
VERDICT: PASS
or
VERDICT: FAIL
or
VERDICT: PARTIAL"""


VERIFICATION_AGENT = BuiltInAgentDefinition(
    agent_type="verification",
    when_to_use=(
        "Verify that implementation work is correct before reporting "
        "completion. Invoke after non-trivial tasks (3+ file edits, "
        "backend/API changes, infrastructure changes). Pass the original "
        "task description, files changed, and approach taken. Produces "
        "a PASS/FAIL/PARTIAL verdict with evidence."
    ),
    get_system_prompt=_verification_prompt,
    tools=["*"],
    disallowed_tools=list(_BUILTIN_DISALLOWED_FOR_READ_ONLY_SPECIALISTS),
    model="inherit",
    background=True,
    read_only=True,
)


# =====================================================================
# Statusline setup (ccmini / Reachy UI)
# =====================================================================

def _statusline_setup_prompt() -> str:
    return """\
You are a status-line setup specialist for ccmini / Reachy Mini interactive hosts.

Your job: help the user configure a **bottom status bar** (session name, cwd, \
model, token usage, etc.).

## Where settings live
- Global JSON: ``~/.ccmini/config.json`` or legacy ``~/.mini_agent/config.json``.
- Fields may include ``statusline_enabled`` (boolean) and host-specific status \
format keys documented for that runtime.
- The UI may expose ``/statusline`` on/off — mention it when relevant.

## Process
1. Ask which shell they use (zsh/bash) if mirroring PS1.
2. To import PS1, read in order: ``~/.zshrc``, ``~/.bashrc``, ``~/.bash_profile``, \
``~/.profile`` and extract ``PS1`` assignments.
3. Convert common PS1 escapes to POSIX shell snippets (e.g. ``\\u`` → \
``$(whoami)``, ``\\w`` → ``$(pwd)``). Prefer ``printf`` when ANSI colors are used.
4. Propose a **short** one-line status string for a dimmed footer; strip trailing \
``$``/``>`` clutter.
5. Apply minimal **Edit**/**Write** changes to the global config when a concrete \
JSON field is documented; otherwise give an exact snippet for the user to paste.

## Output
End with a short checklist of what you changed or what to paste where.
"""


STATUSLINE_SETUP_AGENT = BuiltInAgentDefinition(
    agent_type="statusline-setup",
    when_to_use=(
        "Configuring the bottom status line / PS1-style footer in ccmini. "
        "Use when the user wants to customize statusline text, import PS1, "
        "or align the bar with shell prompts."
    ),
    get_system_prompt=_statusline_setup_prompt,
    tools=["*"],
)


# =====================================================================
# Claude / platform documentation guide
# =====================================================================

_DOCS_MAP_URL = "https://code.claude.com/docs/en/claude_code_docs_map.md"
_PLATFORM_LLM_TXT_URL = "https://platform.claude.com/llms.txt"


def _claude_docs_guide_prompt() -> str:
    return f"""\
You are the **documentation guide** for Claude Code, the Claude Agent SDK, \
and the Claude API.

## Scope
1. **Claude Code** (CLI): install, config, hooks, skills, MCP, IDE, shortcuts.
2. **Agent SDK** (TypeScript/Python): sessions, tools, permissions, hosting.
3. **Claude API**: Messages API, streaming, tool use, beta features.

## Sources (fetch with WebFetch when answering)
- Claude Code docs map: `{_DOCS_MAP_URL}`
- Platform docs index (API + SDK): `{_PLATFORM_LLM_TXT_URL}`

## Rules
- Prefer fetching current docs over guessing. Quote paths and command names accurately.
- If the user asks about **this** repository (ccmini / Reachy), distinguish \
upstream Claude Code behaviour from local extensions.
- Keep answers structured: short summary, then details, then links/paths.
- Do **not** modify the user's project files unless they explicitly ask you to \
edit docs in-repo.
"""


CLAUDE_DOCS_GUIDE_AGENT = BuiltInAgentDefinition(
    agent_type="claude-docs-guide",
    when_to_use=(
        "Questions about Claude Code CLI, Claude Agent SDK, or Claude API "
        "using official documentation. Use for install, configuration, MCP, "
        "permissions, and API usage — not for general coding in the user's repo."
    ),
    get_system_prompt=_claude_docs_guide_prompt,
    tools=["*"],
    disallowed_tools=list(_BUILTIN_DISALLOWED_FOR_READ_ONLY_SPECIALISTS),
    read_only=True,
)


# =====================================================================
# Registry
# =====================================================================

def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def are_explore_plan_agents_enabled() -> bool:
    """Whether Explore and Plan are exposed in the default registry."""
    return _env_truthy("MINI_AGENT_ENABLE_EXPLORE_PLAN_AGENTS", True)


def is_verification_agent_enabled() -> bool:
    """Whether Verification is exposed in the default registry."""
    return _env_truthy("MINI_AGENT_ENABLE_VERIFICATION_AGENT", True)


def _cfg_builtin_guide_enabled(attr: str, env_name: str, default: bool = True) -> bool:
    """Env overrides config file; used for optional built-in guide agents."""
    raw = os.environ.get(env_name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    try:
        from ..config import load_config

        return bool(getattr(load_config(), attr, default))
    except Exception:
        return default


def get_default_builtin_agents() -> list[BuiltInAgentDefinition]:
    """Return the active built-in agents for the main AgentTool."""
    agents: list[BuiltInAgentDefinition] = [GENERAL_PURPOSE_AGENT, WORKER_AGENT]
    if are_explore_plan_agents_enabled():
        agents.extend([EXPLORE_AGENT, PLAN_AGENT])
    if is_verification_agent_enabled():
        agents.append(VERIFICATION_AGENT)
    if _cfg_builtin_guide_enabled(
        "builtin_statusline_guide_agent_enabled",
        "MINI_AGENT_BUILTIN_STATUSLINE_GUIDE_AGENT_ENABLED",
    ):
        agents.append(STATUSLINE_SETUP_AGENT)
    if _cfg_builtin_guide_enabled(
        "builtin_claude_docs_guide_agent_enabled",
        "MINI_AGENT_BUILTIN_CLAUDE_DOCS_GUIDE_AGENT_ENABLED",
    ):
        agents.append(CLAUDE_DOCS_GUIDE_AGENT)
    return agents


def get_named_builtin_agents() -> list[BuiltInAgentDefinition]:
    """Return built-in sidecar agents that should also be registry-visible."""
    return list(_NAMED_BUILTIN_AGENTS.values())


class BuiltInAgentRegistry:
    """Registry of built-in agent definitions.

    Usage::

        registry = BuiltInAgentRegistry()
        agent_def = registry.get("Explore")
        all_types = registry.list_types()
    """

    def __init__(
        self,
        agents: list[BuiltInAgentDefinition] | None = None,
        *,
        include_named_agents: bool = True,
    ) -> None:
        self._agents: dict[str, BuiltInAgentDefinition] = {}
        active = list(agents or get_default_builtin_agents())
        if include_named_agents:
            existing = {agent.agent_type for agent in active}
            for named in get_named_builtin_agents():
                if named.agent_type not in existing:
                    active.append(named)
        for a in active:
            self._agents[a.agent_type] = a

    def get(self, agent_type: str) -> BuiltInAgentDefinition | None:
        return self._agents.get(agent_type)

    def list_types(self) -> list[str]:
        return list(self._agents.keys())

    def list_definitions(self) -> list[BuiltInAgentDefinition]:
        return list(self._agents.values())

    def register(self, definition: BuiltInAgentDefinition) -> None:
        self._agents[definition.agent_type] = definition

    def remove(self, agent_type: str) -> bool:
        return self._agents.pop(agent_type, None) is not None

    def to_when_to_use_text(self) -> str:
        """Generate a summary of available agent types for the system prompt."""
        lines = ["Available agent types:"]
        for a in self._agents.values():
            lines.append(f"- **{a.agent_type}**: {a.when_to_use}")
        return "\n".join(lines)


# =====================================================================
# MagicDocs agent
# =====================================================================

def _magic_docs_prompt() -> str:
    return """\
You are a documentation maintenance specialist. Your job is to keep \
project documentation accurate and up-to-date.

Guidelines:
- Read the existing documentation files carefully before making changes.
- Update only the sections that are stale or incorrect.
- Preserve the existing style and tone of the documentation.
- Do NOT add boilerplate or obvious comments.
- Keep changes minimal and precise — only what is needed.
- If a doc file references code, verify the references are still valid.
- Produce a brief summary of what you changed and why."""


MAGIC_DOCS_AGENT = BuiltInAgentDefinition(
    agent_type="magic_docs",
    when_to_use=(
        "Automated documentation updater. Use after code changes to "
        "synchronize documentation files with the current codebase. "
        "Reads existing docs, identifies stale sections, and applies "
        "targeted updates."
    ),
    get_system_prompt=_magic_docs_prompt,
    tools=["Read", "Edit"],
    model="fast",
    background=True,
    read_only=False,
)


# =====================================================================
# Compact agent
# =====================================================================

def _compact_prompt() -> str:
    return """\
You are a conversation compactor. You receive a long conversation \
history and produce a concise summary that preserves:

1. Key decisions and their rationale
2. Important code locations mentioned (file paths, line numbers)
3. Current task state and progress
4. Any unresolved issues or open questions
5. Tool results that are still relevant

Rules:
- Output ONLY the summary, no preamble or explanation.
- Use bullet points for clarity.
- Preserve exact file paths and code references.
- Keep the summary under 2000 tokens.
- Do NOT invent information not present in the conversation.
- Prioritize recent context over older context."""


COMPACT_AGENT = BuiltInAgentDefinition(
    agent_type="compact",
    when_to_use=(
        "Conversation summarizer for context compaction. Use when the "
        "conversation history is getting long and needs to be compressed "
        "while preserving essential information."
    ),
    get_system_prompt=_compact_prompt,
    tools=[],
    model="fast",
    background=True,
    read_only=True,
)


# =====================================================================
# Memory extraction agent
# =====================================================================

def _memory_extract_prompt() -> str:
    return """\
You are a memory extraction specialist. Your job is to identify and \
persist useful information from conversations into the project's \
memory directory.

Extract these types of information:
- User preferences and coding style choices
- Project conventions and patterns discovered
- Important architectural decisions
- Recurring issues and their solutions
- Environment-specific configuration notes

Rules:
- Write each memory as a concise, self-contained note.
- Use markdown format with clear headings.
- Organize memories by topic (preferences, decisions, patterns, etc.).
- Do NOT duplicate existing memories — read first, then append or update.
- Keep individual memory files focused (one topic per file).
- File names should be descriptive: e.g. ``auth-pattern.md``, \
``user-preferences.md``."""


MEMORY_EXTRACT_AGENT = BuiltInAgentDefinition(
    agent_type="memory_extract",
    when_to_use=(
        "Extract and persist useful memories from conversations. Use at "
        "session end or when important decisions/preferences are expressed. "
        "Reads the conversation, identifies reusable knowledge, and writes "
        "it to the memory directory."
    ),
    get_system_prompt=_memory_extract_prompt,
    tools=["Read", "Grep", "Glob", "Edit", "Write"],
    model="inherit",
    background=True,
    read_only=False,
)


# =====================================================================
# Agent summary agent
# =====================================================================

def _agent_summary_prompt() -> str:
    return """\
Generate a 3-5 word progress summary of the agent's work.

Rules:
- Output ONLY the summary phrase, nothing else.
- Use present tense, active voice.
- Be specific about what was done (e.g. "Fixed auth null pointer", \
not "Made changes").
- Do NOT include punctuation at the end.
- Do NOT include the word "agent" or "summary" in the output."""


AGENT_SUMMARY_AGENT = BuiltInAgentDefinition(
    agent_type="agent_summary",
    when_to_use=(
        "Generate a concise 3-5 word progress summary of an agent's work. "
        "Use when a background task completes and needs a human-readable "
        "status label for the UI."
    ),
    get_system_prompt=_agent_summary_prompt,
    tools=[],
    model="fast",
    background=True,
    read_only=True,
)


# =====================================================================
# Named agent registry / get_builtin_agent()
# =====================================================================

_NAMED_BUILTIN_AGENTS: dict[str, BuiltInAgentDefinition] = {
    "magic_docs": MAGIC_DOCS_AGENT,
    "compact": COMPACT_AGENT,
    "memory_extract": MEMORY_EXTRACT_AGENT,
    "agent_summary": AGENT_SUMMARY_AGENT,
}


def get_builtin_agent(name: str) -> BuiltInAgentDefinition | None:
    """Look up a built-in agent by its short name.

    Valid names: ``"magic_docs"``, ``"compact"``, ``"memory_extract"``,
    ``"agent_summary"``.

    Returns ``None`` if the name is not recognised.
    """
    return _NAMED_BUILTIN_AGENTS.get(name)


def list_builtin_agent_names() -> list[str]:
    """Return the short names of all named built-in agents."""
    return list(_NAMED_BUILTIN_AGENTS.keys())


def register_named_agent(name: str, definition: BuiltInAgentDefinition) -> None:
    """Register an additional named built-in agent."""
    _NAMED_BUILTIN_AGENTS[name] = definition
