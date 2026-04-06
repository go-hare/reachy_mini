"""Multi-agent coordination — spawn and manage sub-agents.

**Agent tool parity** (reference ``src/tools/AgentTool/runAgent.ts``,
``agentToolUtils.ts``, ``prompt.ts``): ``AgentTool`` and ``run_sub_agent`` /
``subagent.run_subagent`` implement delegation, tool resolution
(``_resolve_tools`` ↔ ``resolveAgentTools`` / denylists), fork boilerplate
(``MINI_AGENT_FORK_SUBAGENT`` ↔ ``isForkSubagentEnabled``), and teammate
spawning — not a line-by-line port of the TS monolith (MCP init, transcript
hooks, analytics are host-specific).

**Python-only orchestration helpers** — there is no single named equivalent
in the recovered ``AgentTool/`` tree; they compose ``run_sub_agent`` for apps
and tests:

- ``AgentPool`` — parallel runs
- ``Pipeline`` — sequential stages
- ``Debate`` — panel + judge
- ``Router`` — keyword or ``side_query``-based routing
- ``Handoff`` — parses ``[HANDOFF:role]`` / ``[DONE]`` markers
- ``SharedAgentContext`` — shared KV for multi-agent setups
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from ..messages import (
    CompletionEvent,
    ErrorEvent,
    Message,
    StreamEvent,
    TextBlock,
    TextEvent,
    ToolResultBlock,
    ToolUseBlock,
    user_message,
)
from ..prompts import SystemPrompt
from ..providers import BaseProvider
from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)
_FORK_BOILERPLATE_TAG = "fork-boilerplate"
_FORK_DIRECTIVE_PREFIX = "FORK_DIRECTIVE: "
_FORK_PLACEHOLDER_RESULT = "Fork started - processing in background"


# ======================================================================
# Data structures
# ======================================================================

@dataclass
class SubAgentConfig:
    """Configuration for spawning a sub-agent."""
    name: str = "sub-agent"
    role: str = "general"
    system_prompt: str = ""
    max_turns: int = 10
    tools: list[Tool] = field(default_factory=list)
    model: str = ""


@dataclass
class SubAgentResult:
    """Result from a sub-agent run."""
    agent_id: str
    role: str
    reply: str
    messages: list[Message] = field(default_factory=list)
    success: bool = True
    error: str = ""


@dataclass
class PipelineStageResult:
    """Result from one stage of a pipeline."""
    stage_index: int
    config: SubAgentConfig
    result: SubAgentResult


@dataclass
class PipelineResult:
    """Aggregated result from a full pipeline run."""
    stages: list[PipelineStageResult] = field(default_factory=list)
    final_reply: str = ""
    success: bool = True
    error: str = ""


@dataclass
class DebateResult:
    """Result from a debate round."""
    opinions: list[SubAgentResult] = field(default_factory=list)
    verdict: str = ""
    success: bool = True
    error: str = ""


class HandoffAction(Enum):
    """What the current agent wants to do."""
    CONTINUE = "continue"
    HANDOFF = "handoff"
    DONE = "done"


@dataclass
class HandoffDecision:
    """Parsed from the current agent's reply."""
    action: HandoffAction
    target_role: str = ""
    message: str = ""
    reply: str = ""


# ======================================================================
# Core runner (unchanged)
# ======================================================================

async def run_sub_agent(
    *,
    provider: BaseProvider,
    config: SubAgentConfig,
    prompt: str,
    context_messages: list[Message] | None = None,
    runtime_overrides: dict[str, Any] | None = None,
) -> SubAgentResult:
    """Run a sub-agent to completion and return its result."""
    from .subagent import run_subagent

    agent_id = f"{config.name}-{uuid4().hex[:6]}"
    system = config.system_prompt or f"You are a {config.role} assistant."

    active_provider = provider
    if config.model:
        from ..providers import ProviderConfig, create_provider
        parent_config = getattr(provider, '_config', None)
        if parent_config is not None and isinstance(parent_config, ProviderConfig):
            active_provider = create_provider(ProviderConfig(
                type=parent_config.type,
                model=config.model,
                api_key=parent_config.api_key,
                base_url=parent_config.base_url,
            ))
        else:
            logger.warning(
                "SubAgentConfig.model=%r ignored: parent provider has no "
                "ProviderConfig to inherit from", config.model,
            )

    try:
        reply_text = await run_subagent(
            provider=active_provider,
            system_prompt=system,
            user_text=prompt,
            tools=config.tools,
            parent_messages=copy.deepcopy(context_messages) if context_messages else None,
            max_turns=config.max_turns,
            agent_id=agent_id,
            query_source=str(runtime_overrides.get("query_source", "") if runtime_overrides else ""),
            runtime_overrides=runtime_overrides,
        )
        return SubAgentResult(
            agent_id=agent_id,
            role=config.role,
            reply=reply_text,
            messages=[],
        )
    except Exception as exc:
        return SubAgentResult(
            agent_id=agent_id,
            role=config.role,
            reply="",
            success=False,
            error=str(exc),
        )


# ======================================================================
# AgentTool — LLM-callable delegation
# ======================================================================

class AgentTool(Tool):
    """Tool that delegates work to a sub-agent.

    Supports two selection modes (mirroring Claude Code):

    1. **subagent_type** — selects a built-in agent definition with
       a specialised prompt, tool restrictions, and model.
    2. **role** (legacy) — selects a simple role prompt from the
       ``_role_prompts`` dict.

    When a ``BuiltInAgentRegistry`` is provided, the tool schema
    advertises the available ``subagent_type`` values.
    """

    name = "Agent"
    aliases = ("Task",)
    description = (
        "Launch a new agent to handle complex, multi-step tasks "
        "autonomously. Specify a subagent_type to select a specialised "
        "agent, or omit it for a general-purpose agent."
    )
    is_read_only = True

    def __init__(
        self,
        provider: BaseProvider,
        available_roles: list[str] | None = None,
        *,
        registry: Any | None = None,
        parent_tools: list[Tool] | None = None,
    ) -> None:
        self._provider = provider
        self._roles = available_roles or ["general", "explore", "plan", "code_review"]
        self._parent_tools = list(parent_tools or [])

        from .builtin_agents import BuiltInAgentRegistry
        if registry is not None and isinstance(registry, BuiltInAgentRegistry):
            self._registry: Any = registry
        else:
            self._registry = BuiltInAgentRegistry()

        if os.environ.get("MINI_AGENT_FORK_SUBAGENT", "").strip().lower() in {"1", "true", "yes", "on"}:
            self.description = (
                "Launch a new agent to handle complex, multi-step tasks "
                "autonomously. Specify a subagent_type to select a specialised "
                "agent, or omit it to fork yourself with inherited context."
            )
        self.instructions = self._build_instructions()

    def _build_instructions(self) -> str:
        fork_enabled = os.environ.get("MINI_AGENT_FORK_SUBAGENT", "").strip().lower() in {"1", "true", "yes", "on"}
        parts = [
            "Launch a specialised agent to handle complex tasks autonomously.",
            "",
            "The agent runs independently and returns its result. Use for "
            "complex sub-tasks that benefit from a fresh context or parallel "
            "execution.",
            "",
        ]
        if self._registry:
            parts.append(self._registry.to_when_to_use_text())
            parts.append("")
        parts.extend([
            "Usage notes:",
            (
                "- When spawning a fresh agent with subagent_type, it starts "
                "with zero context — brief it like a smart colleague."
                if fork_enabled
                else "- Always include a clear, detailed prompt. The agent starts "
                "with zero context — brief it like a smart colleague."
            ),
            "- For simple, directed searches, use grep/glob directly.",
            "- For broader exploration, use subagent_type=\"Explore\".",
            "- For implementation planning, use subagent_type=\"Plan\".",
            "- For verifying work, use subagent_type=\"verification\".",
            *(
                [
                    "- Omitting subagent_type forks yourself and inherits the full conversation context.",
                    "- Forks run in the background and completion arrives later as a task notification.",
                ]
                if fork_enabled
                else []
            ),
            "- Set run_in_background=true when you want the agent to keep working while the main conversation continues.",
            "- Set name when you need to address a running background agent later via SendMessage.",
            "- Set team_name together with name to spawn a persistent teammate into a team context.",
            "- Set cwd to scope the agent's filesystem and shell operations to a different working directory.",
            "- isolation=\"worktree\" creates a temporary git worktree and runs the agent there.",
        ])
        return "\n".join(parts)

    def is_concurrency_safe(self, input_data: dict[str, Any] | None = None) -> bool:
        del input_data
        # Agent execution must happen after the calling assistant message is
        # committed so forked children can reconstruct the exact tool_use/tool_result
        # prefix from the parent turn.
        return False

    def get_parameters_schema(self) -> dict[str, Any]:
        agent_types = self._registry.list_types() if self._registry else []
        fork_enabled = os.environ.get("MINI_AGENT_FORK_SUBAGENT", "").strip().lower() in {"1", "true", "yes", "on"}

        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Optional agent name. Makes the spawned agent addressable while running.",
                },
                "description": {
                    "type": "string",
                    "description": "Short label for the spawned worker task.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Detailed task description for the agent.",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "Built-in agent type to use.",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override for this agent.",
                },
                "team_name": {
                    "type": "string",
                    "description": "Optional explicit team name for teammate spawning.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Absolute working directory for this agent's filesystem and shell operations.",
                },
                "mode": {
                    "type": "string",
                    "description": "Optional teammate mode. Use \"plan\" when the teammate should request plan approval before implementation.",
                },
                "isolation": {
                    "type": "string",
                    "enum": ["worktree"],
                    "description": "Isolation mode. \"worktree\" creates a temporary git worktree.",
                },
            },
            "required": ["description", "prompt"],
        }
        if not fork_enabled:
            schema["properties"]["run_in_background"] = {
                "type": "boolean",
                "description": "Set to true to run this agent in the background.",
            }
        if agent_types:
            schema["properties"]["subagent_type"]["enum"] = agent_types
        return schema

    def _resolve_tools(self, definition: Any) -> list[Tool]:
        """Resolve the tool set for a built-in agent definition."""
        if not self._parent_tools:
            return []

        disallowed = set(definition.disallowed_tools)
        if definition.tools == ["*"]:
            return [
                t for t in self._parent_tools
                if t.name not in disallowed and not any(alias in disallowed for alias in getattr(t, "aliases", ()))
            ]

        allowed = set(definition.tools) - disallowed
        return [
            t for t in self._parent_tools
            if t.name in allowed or any(alias in allowed for alias in getattr(t, "aliases", ()))
        ]

    def _resolve_model(self, definition: Any) -> str:
        model = getattr(definition, "model", "") or ""
        if not model or model == "inherit":
            return ""
        if model == "fast":
            try:
                from ..utils.fast_mode import FastModeManager

                return FastModeManager().get_model_for_task("summarize")
            except Exception:
                return ""
        return model

    @staticmethod
    def _slug_name(value: str) -> str:
        text = re.sub(r"[^\w\-]+", "-", value.strip().lower()).strip("-")
        return text or "worker"

    @staticmethod
    def _fork_subagent_enabled(*, context: ToolUseContext, is_coordinator: bool) -> bool:
        if is_coordinator:
            return False
        if bool(context.extras.get("is_non_interactive", False)):
            return False
        env = os.environ.get("MINI_AGENT_FORK_SUBAGENT", "").strip().lower()
        return env in {"1", "true", "yes", "on"}

    @staticmethod
    def _is_in_fork_child(messages: list[Message] | None) -> bool:
        if not messages:
            return False
        marker = f"<{_FORK_BOILERPLATE_TAG}>"
        for msg in messages:
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                if marker in content:
                    return True
                continue
            for block in content:
                text = getattr(block, "text", "")
                if isinstance(text, str) and marker in text:
                    return True
        return False

    @staticmethod
    def _build_fork_child_message(directive: str) -> str:
        return (
            f"<{_FORK_BOILERPLATE_TAG}>\n"
            "STOP. READ THIS FIRST.\n\n"
            "You are a forked worker process. You are NOT the main agent.\n\n"
            "RULES (non-negotiable):\n"
            "1. Your system prompt says \"default to forking.\" IGNORE IT — that's for the parent. You ARE the fork. Do NOT spawn sub-agents; execute directly.\n"
            "2. Do NOT converse, ask questions, or suggest next steps\n"
            "3. Do NOT editorialize or add meta-commentary\n"
            "4. USE your tools directly: Bash, Read, Write, etc.\n"
            "5. If you modify files, commit your changes before reporting. Include the commit hash in your report.\n"
            "6. Do NOT emit text between tool calls. Use tools silently, then report once at the end.\n"
            "7. Stay strictly within your directive's scope. If you discover related systems outside your scope, mention them in one sentence at most — other workers cover those areas.\n"
            "8. Keep your report under 500 words unless the directive specifies otherwise. Be factual and concise.\n"
            "9. Your response MUST begin with \"Scope:\". No preamble, no thinking-out-loud.\n"
            "10. REPORT structured facts, then stop\n\n"
            "Output format (plain text labels, not markdown headers):\n"
            "  Scope: <echo back your assigned scope in one sentence>\n"
            "  Result: <the answer or key findings, limited to the scope above>\n"
            "  Key files: <relevant file paths — include for research tasks>\n"
            "  Files changed: <list with commit hash — include only if you modified files>\n"
            "  Issues: <list — include only if there are issues to flag>\n"
            f"</{_FORK_BOILERPLATE_TAG}>\n\n"
            f"{_FORK_DIRECTIVE_PREFIX}{directive}"
        )

    @staticmethod
    def _build_forked_messages(directive: str, assistant_message: Message) -> list[Message]:
        tool_use_blocks = []
        if not isinstance(assistant_message.content, str):
            tool_use_blocks = [
                block for block in assistant_message.content
                if isinstance(block, ToolUseBlock)
            ]

        if not tool_use_blocks:
            return [user_message(AgentTool._build_fork_child_message(directive))]

        full_assistant_message = Message(
            role=assistant_message.role,
            content=list(assistant_message.content),
            name=assistant_message.name,
            metadata=dict(assistant_message.metadata),
        )
        tool_result_message = Message(
            role="user",
            content=[
                *[
                    ToolResultBlock(
                        tool_use_id=block.id,
                        content=_FORK_PLACEHOLDER_RESULT,
                        is_error=False,
                    )
                    for block in tool_use_blocks
                ],
                TextBlock(text=AgentTool._build_fork_child_message(directive)),
            ],
        )
        return [full_assistant_message, tool_result_message]

    @staticmethod
    def _build_worktree_notice(parent_cwd: str, worktree_cwd: str) -> str:
        return (
            "You've inherited the conversation context above from a parent agent "
            f"working in {parent_cwd}. You are operating in an isolated git "
            f"worktree at {worktree_cwd} — same repository, same relative file "
            "structure, separate working copy. Paths in the inherited context "
            "refer to the parent's working directory; translate them to your "
            "worktree root. Re-read files before editing if the parent may have "
            "modified them since they appear in the context. Your changes stay "
            "in this worktree and will not affect the parent's files."
        )

    def _allocate_teammate_name(self, team: Any, base_name: str) -> str:
        existing = {
            getattr(state.identity, "agent_name", "")
            for state in team.list_teammates()
        }
        candidate = self._slug_name(base_name)
        if candidate not in existing:
            return candidate
        suffix = 2
        while f"{candidate}-{suffix}" in existing:
            suffix += 1
        return f"{candidate}-{suffix}"

    def _spawn_team_teammate(
        self,
        *,
        parent_agent: Any,
        config: SubAgentConfig,
        prompt: str,
        description: str,
        team_name_override: str = "",
        working_directory: str = "",
        plan_mode_required: bool = False,
    ) -> str | None:
        team_tool = getattr(parent_agent, "_team_create_tool", None)
        team_name = team_name_override.strip() or (
            str(getattr(team_tool, "_active_team_name", "")).strip() if team_tool is not None else ""
        )
        if not team_name:
            return None
        team = team_tool.get_team(team_name)
        if team is None:
            return None

        from .teammate import TeammateConfig

        teammate_name = self._allocate_teammate_name(team, description or config.role or config.name)
        agent_id = team.spawn_teammate(
            TeammateConfig(
                name=teammate_name,
                team_name=team_name,
                initial_prompt=prompt,
                system_prompt=config.system_prompt,
                tools=config.tools,
                provider=self._provider,
                max_turns_per_prompt=config.max_turns,
                model=config.model,
                working_directory=working_directory,
                plan_mode_required=plan_mode_required,
            )
        )
        return agent_id

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        description = kwargs.get("description", "").strip()
        name = kwargs.get("name", "").strip()
        prompt = kwargs["prompt"]
        subagent_type = kwargs.get("subagent_type", "").strip()
        model_override = kwargs.get("model", "").strip()
        run_in_background = bool(kwargs.get("run_in_background", False))
        team_name_override = kwargs.get("team_name", "").strip()
        cwd_override = kwargs.get("cwd", "").strip()
        spawn_mode = kwargs.get("mode", "").strip()
        isolation = kwargs.get("isolation", "").strip()
        context_messages = context.messages if isinstance(context.messages, list) else []
        parent_messages = [msg for msg in context_messages if isinstance(msg, Message)]
        runtime_overrides = {
            key: context.extras.get(key)
            for key in (
                "permission_checker",
                "hook_runner",
                "attachment_collector",
                "summary_provider",
                "context_manager",
                "session_memory_content",
                "compact_threshold",
                "fallback_config",
            )
            if context.extras.get(key) is not None
        }
        if not description:
            return "Error: 'description' is required."
        if cwd_override:
            runtime_overrides["working_directory"] = cwd_override
        if cwd_override and isolation == "worktree":
            return "Error: 'cwd' cannot be combined with isolation='worktree'."
        if isolation == "remote":
            return "Remote isolation is not supported by the current mini-agent runtime."
        parent_agent = context.extras.get("agent")
        coordinator_mode = getattr(parent_agent, "_coordinator_mode", None)
        is_coordinator = bool(getattr(coordinator_mode, "is_active", False))
        fork_enabled = self._fork_subagent_enabled(context=context, is_coordinator=is_coordinator)
        effective_run_in_background = run_in_background or fork_enabled
        has_team_target = bool(
            parent_agent is not None
            and name
            and (
                team_name_override
                or str(
                    getattr(
                        getattr(parent_agent, "_team_create_tool", None),
                        "_active_team_name",
                        "",
                    )
                ).strip()
            )
        )

        is_fork_path = not subagent_type and fork_enabled
        requested_type = subagent_type or ("fork" if is_fork_path else "general-purpose")
        worktree = None
        if isolation == "worktree":
            from ..tools.worktree import create_agent_worktree

            base_cwd = str(runtime_overrides.get("working_directory", "")).strip() or os.getcwd()
            worktree = await create_agent_worktree(
                cwd=base_cwd,
                slug=self._slug_name(name or description or requested_type or "agent"),
            )
            runtime_overrides["working_directory"] = worktree.worktree_path
            runtime_overrides["agent_worktree"] = worktree

        if is_fork_path:
            if (
                str(context.extras.get("query_source", "")).strip() == "agent:builtin:fork"
                or self._is_in_fork_child(parent_messages)
            ):
                return "Error: Fork is not available inside a forked worker. Complete your task directly using your tools."

            system_prompt = context.extras.get("system_prompt", "")
            if isinstance(system_prompt, list):
                system_prompt_text = json.dumps(system_prompt, ensure_ascii=False)
            else:
                system_prompt_text = str(system_prompt or "")

            directive = prompt
            if worktree is not None:
                directive = (
                    f"{directive}\n\n"
                    f"{self._build_worktree_notice(os.getcwd(), worktree.worktree_path)}"
                )

            fork_prompt = self._build_fork_child_message(directive)
            fork_history = list(parent_messages)
            fork_prompt_messages: list[Message] | None = None
            if fork_history and fork_history[-1].role == "assistant" and fork_history[-1].has_tool_use:
                last_assistant = fork_history.pop()
                fork_prompt_messages = self._build_forked_messages(directive, last_assistant)
            else:
                fork_prompt_messages = [user_message(fork_prompt)]

            fork_runtime_overrides = dict(runtime_overrides)
            fork_runtime_overrides["query_source"] = "agent:builtin:fork"

            if parent_agent is not None:
                task_id = parent_agent.background_runner.spawn(
                    name=name or description or "fork",
                    prompt=fork_prompt,
                    system_prompt=system_prompt_text,
                    tools=list(self._parent_tools),
                    context_messages=fork_history,
                    max_turns=200,
                    runtime_overrides=fork_runtime_overrides,
                    initial_messages=fork_prompt_messages,
                )
                task_info = parent_agent.background_runner.get_status(task_id)
                output_file = getattr(task_info, "output_file", "") if task_info is not None else ""
                return json.dumps({
                    "status": "async_launched",
                    "agentId": task_id,
                    "description": name or description or "fork",
                    "prompt": prompt,
                    "outputFile": output_file,
                }, ensure_ascii=False)

            from .subagent import ForkedAgentContext, run_forked_agent

            result = await run_forked_agent(
                context=ForkedAgentContext(
                    parent_messages=fork_history,
                    parent_system_prompt=system_prompt_text,
                    can_use_tool=lambda tool_name: any(t.name == tool_name for t in self._parent_tools),
                ),
                prompt_messages=fork_prompt_messages,
                provider=self._provider,
                tools=list(self._parent_tools),
                max_turns=200,
                agent_id=name or "fork",
                query_source="agent:builtin:fork",
            )
            return result.text

        if self._registry:
            definition = self._registry.get(requested_type)
            if definition is not None:
                tools = self._resolve_tools(definition)
                config = SubAgentConfig(
                    name=name or f"sub-{definition.agent_type}",
                    role=definition.agent_type,
                    system_prompt=definition.get_system_prompt(),
                    max_turns=15 if definition.agent_type == "verification" else 10,
                    tools=tools,
                    model=model_override or self._resolve_model(definition),
                )
                if parent_agent is not None and has_team_target:
                    teammate_id = self._spawn_team_teammate(
                        parent_agent=parent_agent,
                        config=config,
                        prompt=prompt,
                        description=name or description or definition.agent_type,
                        team_name_override=team_name_override,
                        working_directory=str(runtime_overrides.get("working_directory", "") or ""),
                        plan_mode_required=spawn_mode == "plan",
                    )
                    if teammate_id is not None:
                        return json.dumps({
                            "status": "teammate_spawned",
                            "prompt": prompt,
                            "teammate_id": teammate_id,
                            "agent_id": teammate_id,
                            "agent_type": definition.agent_type,
                            "name": name or description or definition.agent_type,
                            "team_name": team_name_override or "",
                        }, ensure_ascii=False)
                if parent_agent is not None and effective_run_in_background:
                    task_id = parent_agent.background_runner.spawn(
                        name=name or description or definition.agent_type,
                        prompt=prompt,
                        system_prompt=config.system_prompt,
                        tools=config.tools,
                        context_messages=None,
                        max_turns=config.max_turns,
                        model=config.model,
                        runtime_overrides=runtime_overrides,
                    )
                    task_info = parent_agent.background_runner.get_status(task_id)
                    output_file = getattr(task_info, "output_file", "") if task_info is not None else ""
                    return json.dumps({
                        "status": "async_launched",
                        "agentId": task_id,
                        "description": name or description or definition.agent_type,
                        "prompt": prompt,
                        "outputFile": output_file,
                    }, ensure_ascii=False)
                result = await run_sub_agent(
                    provider=self._provider,
                    config=config,
                    prompt=prompt,
                    context_messages=None,
                    runtime_overrides=runtime_overrides,
                )
                if worktree is not None:
                    from ..tools.worktree import cleanup_agent_worktree

                    cleanup = await cleanup_agent_worktree(worktree)
                    if cleanup.get("status") == "kept" and result.success:
                        result.reply = (
                            f"{result.reply}\n\n"
                            f"[worktree kept at {cleanup['worktree_path']} on branch {cleanup['branch_name']}]"
                        )
                if result.success:
                    return result.reply
                return f"Agent [{definition.agent_type}] error: {result.error}"
            if subagent_type:
                raise ValueError(f"Unknown subagent_type: {subagent_type}")

        role = kwargs.get("role", subagent_type or "general")
        config = SubAgentConfig(
            name=name or f"sub-{role}",
            role=role,
            system_prompt=_role_prompts.get(role, ""),
            max_turns=5,
            model=model_override,
        )

        if parent_agent is not None and has_team_target:
            teammate_id = self._spawn_team_teammate(
                parent_agent=parent_agent,
                config=config,
                prompt=prompt,
                description=name or description or role,
                team_name_override=team_name_override,
                working_directory=str(runtime_overrides.get("working_directory", "") or ""),
                plan_mode_required=spawn_mode == "plan",
            )
            if teammate_id is not None:
                return json.dumps({
                    "status": "teammate_spawned",
                    "prompt": prompt,
                    "teammate_id": teammate_id,
                    "agent_id": teammate_id,
                    "agent_type": role,
                    "name": name or description or role,
                    "team_name": team_name_override or "",
                }, ensure_ascii=False)
        if parent_agent is not None and effective_run_in_background:
            task_id = parent_agent.background_runner.spawn(
                name=name or description or role,
                prompt=prompt,
                system_prompt=config.system_prompt,
                tools=config.tools,
                context_messages=None,
                max_turns=config.max_turns,
                model=config.model,
                runtime_overrides=runtime_overrides,
            )
            task_info = parent_agent.background_runner.get_status(task_id)
            output_file = getattr(task_info, "output_file", "") if task_info is not None else ""
            return json.dumps({
                "status": "async_launched",
                "agentId": task_id,
                "description": name or description or role,
                "prompt": prompt,
                "outputFile": output_file,
            }, ensure_ascii=False)

        result = await run_sub_agent(
            provider=self._provider,
            config=config,
            prompt=prompt,
            context_messages=None,
            runtime_overrides=runtime_overrides,
        )
        if worktree is not None:
            from ..tools.worktree import cleanup_agent_worktree

            cleanup = await cleanup_agent_worktree(worktree)
            if cleanup.get("status") == "kept" and result.success:
                result.reply = (
                    f"{result.reply}\n\n"
                    f"[worktree kept at {cleanup['worktree_path']} on branch {cleanup['branch_name']}]"
                )

        if result.success:
            return result.reply
        return f"Sub-agent error: {result.error}"


# ======================================================================
# AgentPool — concurrent sub-agents
# ======================================================================

class AgentPool:
    """Manage a pool of sub-agents for parallel work."""

    def __init__(self, provider: BaseProvider) -> None:
        self._provider = provider
        self._results: dict[str, SubAgentResult] = {}

    async def run_parallel(
        self,
        tasks: list[tuple[SubAgentConfig, str]],
    ) -> list[SubAgentResult]:
        """Run multiple sub-agents concurrently."""
        coros = [
            run_sub_agent(provider=self._provider, config=cfg, prompt=prompt)
            for cfg, prompt in tasks
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        out: list[SubAgentResult] = []
        for r in results:
            if isinstance(r, SubAgentResult):
                self._results[r.agent_id] = r
                out.append(r)
            else:
                out.append(SubAgentResult(
                    agent_id="error",
                    role="error",
                    reply="",
                    success=False,
                    error=str(r),
                ))
        return out


# ======================================================================
# Pipeline — sequential chain
# ======================================================================

class Pipeline:
    """Sequential agent chain: each stage's output becomes the next stage's input."""

    def __init__(
        self,
        provider: BaseProvider,
        stages: list[SubAgentConfig],
    ) -> None:
        self._provider = provider
        self._stages = list(stages)

    @property
    def stages(self) -> list[SubAgentConfig]:
        return list(self._stages)

    async def run(
        self,
        prompt: str,
        *,
        context_messages: list[Message] | None = None,
        on_step_complete: Callable[[PipelineStageResult], Any] | None = None,
    ) -> PipelineResult:
        """Execute all stages sequentially, threading output → input.

        Args:
            on_step_complete: Optional callback invoked after each stage
                completes. Receives the ``PipelineStageResult``. If the
                callback is a coroutine function, it is awaited.
        """
        pipeline_result = PipelineResult()

        if not self._stages:
            return pipeline_result

        current_input = prompt

        for idx, config in enumerate(self._stages):
            stage_prompt = current_input
            if idx > 0:
                stage_prompt = (
                    f"Previous stage output:\n\n{current_input}\n\n"
                    f"Continue processing based on the above."
                )

            result = await run_sub_agent(
                provider=self._provider,
                config=config,
                prompt=stage_prompt,
                context_messages=context_messages if idx == 0 else None,
            )

            stage = PipelineStageResult(
                stage_index=idx, config=config, result=result,
            )
            pipeline_result.stages.append(stage)

            if on_step_complete is not None:
                ret = on_step_complete(stage)
                if asyncio.iscoroutine(ret):
                    await ret

            if not result.success:
                pipeline_result.success = False
                pipeline_result.error = (
                    f"Stage {idx} ({config.name}) failed: {result.error}"
                )
                logger.warning("Pipeline stage %d (%s) failed: %s",
                               idx, config.name, result.error)
                return pipeline_result

            current_input = result.reply
            logger.debug("Pipeline stage %d (%s) done, output %d chars",
                         idx, config.name, len(result.reply))

        pipeline_result.final_reply = current_input
        return pipeline_result


# ======================================================================
# Debate — parallel opinions + judge
# ======================================================================

class Debate:
    """Multiple agents answer the same question, then a judge synthesizes."""

    def __init__(
        self,
        provider: BaseProvider,
        panelists: list[SubAgentConfig],
        judge: SubAgentConfig | None = None,
    ) -> None:
        self._provider = provider
        self._panelists = list(panelists)
        self._judge = judge or SubAgentConfig(
            name="judge",
            role="judge",
            system_prompt=(
                "You are a fair judge. You will receive multiple opinions on a topic. "
                "Synthesize them into a clear, balanced verdict. Cite the strongest "
                "points from each opinion."
            ),
            max_turns=3,
        )

    @property
    def panelists(self) -> list[SubAgentConfig]:
        return list(self._panelists)

    async def run(
        self,
        prompt: str,
        *,
        context_messages: list[Message] | None = None,
        max_rounds: int = 1,
        convergence_check: Callable[[str, str], bool] | None = None,
    ) -> DebateResult:
        """Collect parallel opinions, then let the judge decide.

        Args:
            max_rounds: Maximum debate rounds. In each round after the
                first, panelists see the previous verdict and can refine.
            convergence_check: Optional callable ``(prev_verdict,
                new_verdict) -> bool``. If it returns ``True`` the
                debate stops early (the panelists have converged).
        """
        prev_verdict = ""

        for round_num in range(max_rounds):
            round_prompt = prompt
            if round_num > 0 and prev_verdict:
                round_prompt = (
                    f"{prompt}\n\n"
                    f"Previous round verdict:\n{prev_verdict}\n\n"
                    f"Refine your opinion considering this verdict."
                )

            pool = AgentPool(self._provider)
            tasks = [(cfg, round_prompt) for cfg in self._panelists]
            opinions = await pool.run_parallel(tasks)

            debate_result = DebateResult(opinions=opinions)

            failed = [o for o in opinions if not o.success]
            if len(failed) == len(opinions):
                debate_result.success = False
                debate_result.error = "All panelists failed."
                return debate_result

            opinion_text = _format_opinions(opinions)
            judge_prompt = (
                f"Original question: {prompt}\n\n"
                f"Opinions from the panel (round {round_num + 1}):\n\n"
                f"{opinion_text}\n\n"
                f"Please synthesize a verdict."
            )

            judge_result = await run_sub_agent(
                provider=self._provider,
                config=self._judge,
                prompt=judge_prompt,
            )

            if judge_result.success:
                debate_result.verdict = judge_result.reply
            else:
                debate_result.success = False
                debate_result.error = f"Judge failed: {judge_result.error}"
                return debate_result

            if (
                convergence_check is not None
                and prev_verdict
                and convergence_check(prev_verdict, debate_result.verdict)
            ):
                logger.debug(
                    "Debate converged at round %d", round_num + 1,
                )
                return debate_result

            prev_verdict = debate_result.verdict

        return debate_result


# ======================================================================
# Router — classify and dispatch
# ======================================================================

class Router:
    """Route tasks to the best-fit agent based on classification."""

    def __init__(
        self,
        provider: BaseProvider,
        routes: dict[str, SubAgentConfig],
        *,
        fallback: SubAgentConfig | None = None,
        fallback_agent: SubAgentConfig | None = None,
        strategy: str = "keyword",
    ) -> None:
        self._provider = provider
        self._routes = dict(routes)
        self._fallback = fallback_agent or fallback or SubAgentConfig(
            name="fallback", role="general",
        )
        self._strategy = strategy

    @property
    def route_names(self) -> list[str]:
        return list(self._routes.keys())

    @property
    def fallback_agent(self) -> SubAgentConfig:
        return self._fallback

    @fallback_agent.setter
    def fallback_agent(self, config: SubAgentConfig) -> None:
        self._fallback = config

    async def route(
        self,
        prompt: str,
        *,
        context_messages: list[Message] | None = None,
        force_route: str | None = None,
    ) -> SubAgentResult:
        """Classify the prompt and dispatch to the matching agent.

        When no route matches and no ``force_route`` is given, the
        ``fallback_agent`` handles the request.
        """
        if force_route and force_route in self._routes:
            chosen = force_route
        elif self._strategy == "llm":
            chosen = await self._classify_llm(prompt)
        else:
            chosen = self._classify_keyword(prompt)

        config = self._routes.get(chosen, self._fallback)
        logger.info("Router dispatching to %r (strategy=%s)", chosen, self._strategy)

        return await run_sub_agent(
            provider=self._provider,
            config=config,
            prompt=prompt,
            context_messages=context_messages,
        )

    def _classify_keyword(self, prompt: str) -> str:
        """Simple keyword matching against route names."""
        prompt_lower = prompt.lower()
        for route_name in self._routes:
            if route_name.lower() in prompt_lower:
                return route_name
        return ""

    async def _classify_llm(self, prompt: str) -> str:
        """Ask the LLM to pick the best route."""
        from .fork import side_query

        route_list = ", ".join(self._routes.keys())
        classification = await side_query(
            provider=self._provider,
            system_prompt=(
                f"You are a task classifier. Given a user request, respond with "
                f"exactly one of these category names: {route_list}\n"
                f"If none fit, respond with: fallback\n"
                f"Respond with ONLY the category name, nothing else."
            ),
            prompt=prompt,
            max_tokens=50,
        )
        chosen = classification.strip().lower()
        if chosen not in self._routes:
            logger.debug("LLM classified as %r, not in routes, using fallback", chosen)
            return ""
        return chosen


# ======================================================================
# Handoff — control transfer between agents
# ======================================================================

class Handoff:
    """Multi-turn conversation where agents hand off control to each other."""

    HANDOFF_PREFIX = "[HANDOFF:"
    DONE_MARKER = "[DONE]"

    def __init__(
        self,
        provider: BaseProvider,
        agents: dict[str, SubAgentConfig],
        *,
        initial_agent: str = "",
        max_handoffs: int = 10,
    ) -> None:
        self._provider = provider
        self._agents = dict(agents)
        self._initial = initial_agent or next(iter(agents), "")
        self._max_handoffs = max_handoffs

    @property
    def agent_names(self) -> list[str]:
        return list(self._agents.keys())

    async def run(
        self,
        prompt: str,
        *,
        context_messages: list[Message] | None = None,
    ) -> list[SubAgentResult]:
        """Run the handoff chain until DONE or max_handoffs is reached."""
        results: list[SubAgentResult] = []
        current_role = self._initial
        current_prompt = prompt

        for step in range(self._max_handoffs):
            config = self._agents.get(current_role)
            if config is None:
                logger.warning("Handoff target %r not found, stopping", current_role)
                break

            result = await run_sub_agent(
                provider=self._provider,
                config=config,
                prompt=current_prompt,
                context_messages=context_messages,
            )
            results.append(result)

            if not result.success:
                logger.warning("Handoff step %d (%s) failed: %s",
                               step, current_role, result.error)
                break

            decision = self._parse_decision(result.reply)

            if decision.action == HandoffAction.DONE:
                logger.debug("Handoff chain done at step %d (%s)", step, current_role)
                if decision.reply:
                    results[-1] = SubAgentResult(
                        agent_id=result.agent_id,
                        role=result.role,
                        reply=decision.reply,
                        messages=result.messages,
                        success=True,
                    )
                break

            if decision.action == HandoffAction.HANDOFF:
                target = decision.target_role
                if target not in self._agents:
                    logger.warning("Handoff to unknown agent %r, stopping", target)
                    break
                logger.debug("Handoff: %s → %s", current_role, target)
                current_role = target
                current_prompt = decision.message or result.reply
                context_messages = result.messages
                continue

            # CONTINUE — same agent, shouldn't happen in single-turn run_sub_agent
            break

        return results

    def _parse_decision(self, reply: str) -> HandoffDecision:
        """Extract handoff instructions from the agent's reply."""
        if self.DONE_MARKER in reply:
            clean = reply.replace(self.DONE_MARKER, "").strip()
            return HandoffDecision(
                action=HandoffAction.DONE, reply=clean,
            )

        idx = reply.find(self.HANDOFF_PREFIX)
        if idx != -1:
            rest = reply[idx + len(self.HANDOFF_PREFIX):]
            bracket = rest.find("]")
            if bracket != -1:
                target = rest[:bracket].strip()
                message = rest[bracket + 1:].strip()
                clean_reply = reply[:idx].strip()
                return HandoffDecision(
                    action=HandoffAction.HANDOFF,
                    target_role=target,
                    message=message or clean_reply,
                    reply=clean_reply,
                )

        return HandoffDecision(action=HandoffAction.CONTINUE, reply=reply)


# ======================================================================
# Helpers
# ======================================================================

def _format_opinions(opinions: list[SubAgentResult]) -> str:
    """Format panelist opinions for the judge."""
    parts: list[str] = []
    for i, o in enumerate(opinions, 1):
        status = "OK" if o.success else "FAILED"
        header = f"--- Panelist {i} ({o.role}) [{status}] ---"
        body = o.reply if o.success else f"Error: {o.error}"
        parts.append(f"{header}\n{body}")
    return "\n\n".join(parts)


_role_prompts: dict[str, str] = {
    "general": "You are a helpful general-purpose assistant.",
    "explore": "You are a code exploration specialist. Search thoroughly and report findings.",
    "plan": "You are a planning specialist. Break down tasks and propose approaches.",
    "code_review": "You are a code review specialist. Analyze code for bugs, style, and improvements.",
}


# ======================================================================
# SharedAgentContext — thread-safe shared state for multi-agent setups
# ======================================================================

class SharedAgentContext:
    """Thread-safe shared state accessible by all agents in a multi-agent run.

    Provides a simple key-value store with subscription support so agents
    can react to state changes made by their peers.  All mutations are
    protected by an ``asyncio.Lock`` to prevent concurrent-write races.

    Usage::

        ctx = SharedAgentContext()

        # Agent A sets a value
        await ctx.set("target_module", "src/api/handlers.py")

        # Agent B reads it
        path = ctx.get("target_module")

        # Agent C subscribes to changes
        async def on_change(key: str, value: Any) -> None:
            print(f"{key} changed to {value}")
        ctx.subscribe("target_module", on_change)
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, list[Callable[..., Any]]] = {}

    def get(self, key: str, default: Any = None) -> Any:
        """Read a value (lock-free; dict reads are atomic in CPython)."""
        return self._data.get(key, default)

    def get_all(self) -> dict[str, Any]:
        """Return a shallow copy of the entire state."""
        return dict(self._data)

    async def set(self, key: str, value: Any) -> None:
        """Set a value and notify subscribers."""
        async with self._lock:
            self._data[key] = value
        await self._notify(key, value)

    async def update(self, mapping: dict[str, Any]) -> None:
        """Atomically set multiple keys and notify subscribers."""
        async with self._lock:
            self._data.update(mapping)
        for k, v in mapping.items():
            await self._notify(k, v)

    async def delete(self, key: str) -> bool:
        """Remove a key. Returns ``True`` if the key existed."""
        async with self._lock:
            existed = key in self._data
            self._data.pop(key, None)
        if existed:
            await self._notify(key, None)
        return existed

    def subscribe(
        self,
        key: str,
        callback: Callable[..., Any],
    ) -> Callable[[], None]:
        """Register a callback for changes to *key*.

        The callback signature is ``(key: str, value: Any) -> None``
        (or an async variant).  Returns an unsubscribe function.
        """
        self._subscribers.setdefault(key, []).append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers[key].remove(callback)
            except (KeyError, ValueError):
                pass

        return _unsubscribe

    def subscribe_all(
        self,
        callback: Callable[..., Any],
    ) -> Callable[[], None]:
        """Register a callback that fires on *any* key change.

        Uses the sentinel key ``"*"``.
        """
        return self.subscribe("*", callback)

    async def keys(self) -> list[str]:
        """Return the current key list."""
        async with self._lock:
            return list(self._data.keys())

    async def snapshot(self) -> dict[str, Any]:
        """Return a shallow copy of the entire context."""
        async with self._lock:
            return dict(self._data)

    def unsubscribe(self, key: str, callback: Callable[..., Any]) -> bool:
        """Remove a previously registered callback."""
        subs = self._subscribers.get(key, [])
        try:
            subs.remove(callback)
            return True
        except ValueError:
            return False

    async def _notify(self, key: str, value: Any) -> None:
        for cb in self._subscribers.get(key, []):
            try:
                ret = cb(key, value)
                if asyncio.iscoroutine(ret):
                    await ret
            except Exception:
                logger.debug("SharedAgentContext subscriber error for key=%s", key)
        for cb in self._subscribers.get("*", []):
            try:
                ret = cb(key, value)
                if asyncio.iscoroutine(ret):
                    await ret
            except Exception:
                logger.debug("SharedAgentContext wildcard subscriber error for key=%s", key)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)
