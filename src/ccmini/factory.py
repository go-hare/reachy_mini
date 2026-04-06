"""Convenience factory helpers for ccmini hosts."""

from __future__ import annotations

import os
from typing import Any

from .agent import Agent, AgentConfig, ToolProfile
from .attachments import AttachmentCollector
from .hooks import Hook
from .profiles import RuntimeProfile, build_agent_config
from .prompts import SystemPrompt
from .providers import BaseProvider, ProviderConfig, create_provider
from .providers.fallback import FallbackConfig
from .services.skill_prefetch import SkillPrefetch
from .skills import SkillLoader, discover_skill_dirs_for_path
from .token_budget import TokenBudget
from .tool import Tool
from .tools import build_default_tools


def _build_default_skill_prefetch() -> SkillPrefetch | None:
    """Create a local-skill prefetcher for the current working directory."""
    try:
        skill_dirs = discover_skill_dirs_for_path(os.getcwd())
        if not skill_dirs:
            return None
        loader = SkillLoader(skill_dirs=skill_dirs)
        loader.discover()
        return SkillPrefetch(skill_loader=loader)
    except Exception:
        return None


def create_agent(
    *,
    provider: ProviderConfig | BaseProvider,
    system_prompt: str | SystemPrompt,
    profile: RuntimeProfile | str = RuntimeProfile.ROBOT_BRAIN,
    tools: list[Tool] | None = None,
    sub_agent_tools: list[Tool] | None = None,
    tool_profiles: dict[str, ToolProfile] | None = None,
    hooks: list[Hook] | None = None,
    config: AgentConfig | None = None,
    conversation_id: str | None = None,
    agent_id: str = "agent",
    attachment_collector: AttachmentCollector | None = None,
    fallback_config: FallbackConfig | None = None,
    token_budget: TokenBudget | None = None,
    summary_provider: Any | None = None,
    skill_prefetch: Any | None = None,
) -> Agent:
    """Create an Agent using the defaults of a named runtime profile."""

    effective_config = build_agent_config(profile, base=config)
    effective_provider = (
        create_provider(provider)
        if isinstance(provider, ProviderConfig)
        else provider
    )
    effective_skill_prefetch = skill_prefetch or _build_default_skill_prefetch()

    agent = Agent(
        provider=effective_provider,
        system_prompt=system_prompt,
        tools=list(tools or []),
        sub_agent_tools=sub_agent_tools,
        tool_profiles=tool_profiles,
        hooks=hooks,
        config=effective_config,
        conversation_id=conversation_id,
        agent_id=agent_id,
        attachment_collector=attachment_collector,
        fallback_config=fallback_config,
        token_budget=token_budget,
        summary_provider=summary_provider,
        skill_prefetch=effective_skill_prefetch,
    )
    if tools is None:
        assembly = build_default_tools(
            profile,
            provider=effective_provider,
            background_runner=agent.background_runner,
            registry=agent._command_registry,
            extra_tools=list(getattr(agent, "_plugin_tools", [])),
            kairos_gate=agent._config.kairos_gate_config,
        )
        agent._tools = list(assembly.tools)
        agent._current_turn_tools = list(assembly.tools)
        if assembly.team_create_tool is not None:
            agent._team_create_tool = assembly.team_create_tool
    return agent


def create_coding_agent(
    *,
    provider: ProviderConfig | BaseProvider,
    system_prompt: str | SystemPrompt,
    tools: list[Tool] | None = None,
    sub_agent_tools: list[Tool] | None = None,
    tool_profiles: dict[str, ToolProfile] | None = None,
    hooks: list[Hook] | None = None,
    config: AgentConfig | None = None,
    conversation_id: str | None = None,
    agent_id: str = "agent",
    attachment_collector: AttachmentCollector | None = None,
    fallback_config: FallbackConfig | None = None,
    token_budget: TokenBudget | None = None,
    summary_provider: Any | None = None,
    skill_prefetch: Any | None = None,
) -> Agent:
    """Create an agent with the coding-assistant profile defaults."""

    return create_agent(
        provider=provider,
        system_prompt=system_prompt,
        profile=RuntimeProfile.CODING_ASSISTANT,
        tools=tools,
        sub_agent_tools=sub_agent_tools,
        tool_profiles=tool_profiles,
        hooks=hooks,
        config=config,
        conversation_id=conversation_id,
        agent_id=agent_id,
        attachment_collector=attachment_collector,
        fallback_config=fallback_config,
        token_budget=token_budget,
        summary_provider=summary_provider,
        skill_prefetch=skill_prefetch,
    )


def create_robot_agent(
    *,
    provider: ProviderConfig | BaseProvider,
    system_prompt: str | SystemPrompt,
    tools: list[Tool] | None = None,
    sub_agent_tools: list[Tool] | None = None,
    tool_profiles: dict[str, ToolProfile] | None = None,
    hooks: list[Hook] | None = None,
    config: AgentConfig | None = None,
    conversation_id: str | None = None,
    agent_id: str = "agent",
    attachment_collector: AttachmentCollector | None = None,
    fallback_config: FallbackConfig | None = None,
    token_budget: TokenBudget | None = None,
    summary_provider: Any | None = None,
    skill_prefetch: Any | None = None,
) -> Agent:
    """Create an agent with the robot-brain profile defaults."""

    return create_agent(
        provider=provider,
        system_prompt=system_prompt,
        profile=RuntimeProfile.ROBOT_BRAIN,
        tools=tools,
        sub_agent_tools=sub_agent_tools,
        tool_profiles=tool_profiles,
        hooks=hooks,
        config=config,
        conversation_id=conversation_id,
        agent_id=agent_id,
        attachment_collector=attachment_collector,
        fallback_config=fallback_config,
        token_budget=token_budget,
        summary_provider=summary_provider,
        skill_prefetch=skill_prefetch,
    )
