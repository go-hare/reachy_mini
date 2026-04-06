"""High-level runtime profiles for ccmini hosts.

Profiles do not remove capabilities from the shared core. They only choose
which features are enabled by default for a given host style.
"""

from __future__ import annotations

from dataclasses import replace
from enum import Enum

from .agent import AgentConfig


class RuntimeProfile(str, Enum):
    """Default host profile for a ccmini agent instance."""

    CODING_ASSISTANT = "coding_assistant"
    ROBOT_BRAIN = "robot_brain"


def build_agent_config(
    profile: RuntimeProfile | str,
    *,
    base: AgentConfig | None = None,
) -> AgentConfig:
    """Return an AgentConfig with sensible defaults for the chosen profile."""

    cfg = replace(base) if base is not None else AgentConfig()
    selected = RuntimeProfile(profile)

    if selected == RuntimeProfile.CODING_ASSISTANT:
        cfg.enable_builtin_commands = True
        cfg.enable_bundled_skills = True
        cfg.runtime_non_interactive = False
        return cfg

    if selected == RuntimeProfile.ROBOT_BRAIN:
        cfg.enable_builtin_commands = False
        cfg.enable_bundled_skills = False
        cfg.runtime_non_interactive = True
        return cfg

    return cfg


def coding_assistant_config(*, base: AgentConfig | None = None) -> AgentConfig:
    """Convenience wrapper for the coding-assistant profile."""

    return build_agent_config(RuntimeProfile.CODING_ASSISTANT, base=base)


def robot_brain_config(*, base: AgentConfig | None = None) -> AgentConfig:
    """Convenience wrapper for the robot-brain profile."""

    return build_agent_config(RuntimeProfile.ROBOT_BRAIN, base=base)
