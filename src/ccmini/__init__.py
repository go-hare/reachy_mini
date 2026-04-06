"""ccmini shared core package."""

from .agent import Agent, AgentConfig, ToolProfile
from .attachments import (
    AttachmentCollector,
    AttachmentSource,
    CompanionIntroSource,
    ensure_companion_intro_source,
)
from .buddy import (
    BuddyCommand,
    Companion,
    CompanionBones,
    CompanionObserver,
    CompanionRenderState,
    CompanionSoul,
    NurtureEngine,
    StoredCompanion,
    companion_intro_text,
    companion_reserved_columns,
    companion_user_id,
    get_companion,
    get_companion_intro_attachment,
    hatch_companion,
    render_companion,
)
from .distribution_plugins import (
    GROUP_HOOKS,
    GROUP_TOOLS,
    load_hooks_from_entry_points,
    load_tools_from_entry_points,
)
from .factory import create_agent, create_coding_agent, create_robot_agent
from .plugins import (
    LoadedPlugin,
    PluginManifest,
    PluginRegistry,
    discover_plugin_dirs_for_path,
    load_manifest_file,
)
from .profiles import (
    RuntimeProfile,
    build_agent_config,
    coding_assistant_config,
    robot_brain_config,
)
from .services import (
    MagicDocsHook,
    PromptSuggestionHook,
    generate_tool_use_summary,
    get_current_suggestion,
)
from .services.skill_prefetch import PrefetchHandle, SkillPrefetch

__all__ = [
    "Agent",
    "AgentConfig",
    "AttachmentCollector",
    "AttachmentSource",
    "BuddyCommand",
    "Companion",
    "CompanionBones",
    "CompanionIntroSource",
    "CompanionObserver",
    "CompanionRenderState",
    "CompanionSoul",
    "NurtureEngine",
    "MagicDocsHook",
    "LoadedPlugin",
    "PrefetchHandle",
    "PluginManifest",
    "PluginRegistry",
    "PromptSuggestionHook",
    "SkillPrefetch",
    "StoredCompanion",
    "ToolProfile",
    "create_agent",
    "create_coding_agent",
    "create_robot_agent",
    "discover_plugin_dirs_for_path",
    "companion_intro_text",
    "companion_reserved_columns",
    "companion_user_id",
    "RuntimeProfile",
    "build_agent_config",
    "coding_assistant_config",
    "ensure_companion_intro_source",
    "get_companion",
    "get_companion_intro_attachment",
    "get_current_suggestion",
    "generate_tool_use_summary",
    "GROUP_HOOKS",
    "GROUP_TOOLS",
    "hatch_companion",
    "load_hooks_from_entry_points",
    "load_tools_from_entry_points",
    "load_manifest_file",
    "render_companion",
    "robot_brain_config",
]
