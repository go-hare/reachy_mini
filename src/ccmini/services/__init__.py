"""Services — higher-level functions built on the engine primitives.

**Reference mapping (recovered ``cli-js-map``):** many modules parallel
``src/services/*`` or ``src/utils/*`` — e.g. ``side_query`` ↔ ``utils/sideQuery.ts``
(Review #2), session memory, memdir, tool-use summaries. **CLI / SDK 子系统**
(``asciicast``, ``tips``, ``structured_io``) are **functionally aligned** with
``utils/asciicast.ts``, ``services/tips/*``, ``cli/structuredIO.ts``; wiring is
via ``StructuredIO`` for NDJSON hosts where used, not omitted from alignment — see
``FULL_ALIGNMENT_ROADMAP.md`` §5. Non-CLI embedders use the subset imported by
``agent.py`` / ``engine/*`` plus optional ``StructuredIO`` for NDJSON hosts.

Main-chain re-exports also include ``task_budget``, ``relevant_memory_prefetch``,
and ``bg_sessions`` (below).

See ``FULL_ALIGNMENT_ROADMAP.md`` §5 and ``delegation/REVIEW_LOG.md`` Review #9.
"""

from .side_query import (
    SideQueryOptions,
    SideQueryResult,
    side_query,
    side_query_classify,
    side_query_text,
)
from .session_memory import (
    DEFAULT_TEMPLATE as SESSION_MEMORY_TEMPLATE,
    SessionMemoryConfig,
    SessionMemoryHook,
    SessionMemoryState,
    extract_session_memory,
    get_session_memory_content,
    get_session_memory_state,
    is_session_memory_empty,
    reset_session_memory_state,
    should_extract_memory,
    truncate_for_compact,
    wait_for_extraction,
)
from .memdir import (
    MemoryHeader,
    RelevantMemory,
    find_relevant_memories,
    format_memory_manifest,
    get_memory_dir,
    is_memory_path,
    load_relevant_memory_content,
    parse_frontmatter,
    scan_memory_files,
)
from .extract_memories import (
    ExtractMemoriesHook,
    ExtractMemoriesState,
    extract_memories,
    get_extract_state,
    reset_extract_state,
)
from .auto_dream import (
    AutoDreamConfig,
    AutoDreamHook,
    AutoDreamState,
    get_auto_dream_state,
    reset_auto_dream_state,
    run_consolidation,
    should_consolidate,
)
from .magic_docs import (
    MagicDocInfo,
    MagicDocsHook,
    build_update_prompt as build_magic_docs_prompt,
    check_and_register as check_magic_doc,
    clear_tracked_docs as clear_magic_docs,
    detect_magic_doc_header,
    get_tracked_docs as get_magic_docs,
    register_magic_doc,
    update_all_magic_docs,
)
from .prompt_suggestion import (
    PromptSuggestionConfig,
    PromptSuggestionHook,
    PromptSuggestionState,
    clear_suggestion,
    generate_suggestion,
    get_current_suggestion,
    get_suppression_reason,
    should_filter_suggestion,
)
from .away_summary import (
    AwaySummaryManager,
    generate_away_summary,
)
from .voice import (
    AudioBackend,
    VoiceError,
    cancel_recording,
    check_recording_availability,
    check_voice_dependencies,
    get_recording_duration,
    is_recording,
    record_and_transcribe,
    register_stt,
    start_recording,
    stop_recording,
)
from .computer_use import (
    ClickResult,
    ComputerExecutor,
    ScreenshotResult,
    TypeResult,
    acquire_session_lock,
    check_computer_use_availability,
    get_executor,
    release_session_lock,
)
from .structured_io import (
    StructuredIO,
)
from .tips import (
    Tip,
    TipContext,
    clear_tips,
    get_all_tips,
    get_sessions_since_shown,
    get_tip_text,
    init_builtin_tips,
    record_tip_shown,
    register_custom_tip,
    register_tip,
    select_tip,
)
from .prevent_sleep import (
    force_stop as force_stop_prevent_sleep,
    is_active as is_prevent_sleep_active,
    start_prevent_sleep,
    stop_prevent_sleep,
)
from .asciicast import (
    AsciicastRecorder,
    get_session_recordings,
)
from .notifier import (
    NotifChannel,
    send_notification,
)
from .cleanup import (
    CleanupRegistry,
    get_cleanup_registry,
    register_cleanup,
    run_background_cleanup,
)
from .stats import (
    ModelUsage,
    RequestRecord,
    StatsTracker,
    estimate_cost,
    load_cumulative_stats,
)
from .task_budget import (
    apply_usage_to_task_budget,
    attach_task_budget_to_agent,
)
from .tool_use_summary import (
    generate_batch_summary,
    generate_tool_use_summary,
    generate_tool_use_summary_noninteractive,
    generate_tool_use_summary_with_abort,
    get_summary_provider,
)
from .relevant_memory_prefetch import (
    await_relevant_memory_prefetch_if_enabled,
    is_relevant_memory_prefetch_enabled,
)
from .bg_sessions import (
    append_bg_session_record,
    is_bg_sessions_log_enabled,
)

__all__ = [
    # Side Query
    "SideQueryOptions",
    "SideQueryResult",
    "side_query",
    "side_query_classify",
    "side_query_text",
    # Session Memory
    "SESSION_MEMORY_TEMPLATE",
    "SessionMemoryConfig",
    "SessionMemoryHook",
    "SessionMemoryState",
    "extract_session_memory",
    "get_session_memory_content",
    "get_session_memory_state",
    "is_session_memory_empty",
    "reset_session_memory_state",
    "should_extract_memory",
    "truncate_for_compact",
    "wait_for_extraction",
    # Memdir
    "MemoryHeader",
    "RelevantMemory",
    "find_relevant_memories",
    "format_memory_manifest",
    "get_memory_dir",
    "is_memory_path",
    "load_relevant_memory_content",
    "parse_frontmatter",
    "scan_memory_files",
    # Extract Memories
    "ExtractMemoriesHook",
    "ExtractMemoriesState",
    "extract_memories",
    "get_extract_state",
    "reset_extract_state",
    # AutoDream
    "AutoDreamConfig",
    "AutoDreamHook",
    "AutoDreamState",
    "get_auto_dream_state",
    "reset_auto_dream_state",
    "run_consolidation",
    "should_consolidate",
    # Magic Docs
    "MagicDocInfo",
    "MagicDocsHook",
    "build_magic_docs_prompt",
    "check_magic_doc",
    "clear_magic_docs",
    "detect_magic_doc_header",
    "get_magic_docs",
    "register_magic_doc",
    "update_all_magic_docs",
    # Prompt Suggestion
    "PromptSuggestionConfig",
    "PromptSuggestionHook",
    "PromptSuggestionState",
    "clear_suggestion",
    "generate_suggestion",
    "get_current_suggestion",
    "get_suppression_reason",
    "should_filter_suggestion",
    # Away Summary
    "AwaySummaryManager",
    "generate_away_summary",
    # Voice
    "AudioBackend",
    "VoiceError",
    "cancel_recording",
    "check_recording_availability",
    "check_voice_dependencies",
    "get_recording_duration",
    "is_recording",
    "record_and_transcribe",
    "register_stt",
    "start_recording",
    "stop_recording",
    # Computer Use
    "ClickResult",
    "ComputerExecutor",
    "ScreenshotResult",
    "TypeResult",
    "acquire_session_lock",
    "check_computer_use_availability",
    "get_executor",
    "release_session_lock",
    # Structured I/O
    "StructuredIO",
    # Tips
    "Tip",
    "TipContext",
    "clear_tips",
    "get_all_tips",
    "get_sessions_since_shown",
    "get_tip_text",
    "init_builtin_tips",
    "record_tip_shown",
    "register_custom_tip",
    "register_tip",
    "select_tip",
    # Prevent Sleep
    "force_stop_prevent_sleep",
    "is_prevent_sleep_active",
    "start_prevent_sleep",
    "stop_prevent_sleep",
    # Asciicast
    "AsciicastRecorder",
    "get_session_recordings",
    # Notifier
    "NotifChannel",
    "send_notification",
    # Cleanup
    "CleanupRegistry",
    "get_cleanup_registry",
    "register_cleanup",
    "run_background_cleanup",
    # Stats
    "ModelUsage",
    "RequestRecord",
    "StatsTracker",
    "estimate_cost",
    "load_cumulative_stats",
    # Task budget / prefetch / BG sessions (main-chain parity)
    "apply_usage_to_task_budget",
    "attach_task_budget_to_agent",
    "generate_batch_summary",
    "generate_tool_use_summary",
    "generate_tool_use_summary_noninteractive",
    "generate_tool_use_summary_with_abort",
    "get_summary_provider",
    "await_relevant_memory_prefetch_if_enabled",
    "is_relevant_memory_prefetch_enabled",
    "append_bg_session_record",
    "is_bg_sessions_log_enabled",
]
