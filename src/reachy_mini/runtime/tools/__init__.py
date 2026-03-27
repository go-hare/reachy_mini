"""Built-in runtime tools shared by all app profiles."""

from __future__ import annotations

from pathlib import Path

from .file_tools import (
    DeleteLinesTool,
    EditFileTool,
    InsertLinesTool,
    ListDirTool,
    ReadFileTool,
    ReplaceLinesTool,
    SearchFilesTool,
    WriteFileTool,
)
from .reachy_tools import (
    CameraTool,
    DanceTool,
    DoNothingTool,
    HeadTrackingTool,
    MoveHeadTool,
    PlayEmotionTool,
    ReachyToolContext,
    StopDanceTool,
    StopEmotionTool,
)


def build_kernel_system_tools(
    workspace_root: Path,
    *,
    runtime_context: ReachyToolContext | None = None,
) -> list[object]:
    """Build the task-side system tools visible to the kernel."""
    workspace = workspace_root.resolve()
    return [
        ReadFileTool(workspace=workspace, allowed_dir=workspace),
        WriteFileTool(workspace=workspace, allowed_dir=workspace),
        EditFileTool(workspace=workspace, allowed_dir=workspace),
        ListDirTool(workspace=workspace, allowed_dir=workspace),
        SearchFilesTool(workspace=workspace, allowed_dir=workspace),
        InsertLinesTool(workspace=workspace, allowed_dir=workspace),
        DeleteLinesTool(workspace=workspace, allowed_dir=workspace),
        ReplaceLinesTool(workspace=workspace, allowed_dir=workspace),
    ]


def build_front_system_tools(
    *,
    runtime_context: ReachyToolContext | None = None,
) -> list[object]:
    """Build the expressive tool set owned by the front layer."""
    context = runtime_context or ReachyToolContext()
    return [
        MoveHeadTool(context=context),
        DoNothingTool(context=context),
        HeadTrackingTool(context=context),
        CameraTool(context=context),
        PlayEmotionTool(context=context),
        DanceTool(context=context),
        StopEmotionTool(context=context),
        StopDanceTool(context=context),
    ]


def build_system_tools(
    workspace_root: Path,
    *,
    runtime_context: ReachyToolContext | None = None,
) -> list[object]:
    """Build the full built-in tool set for compatibility with older callers."""
    return [
        *build_kernel_system_tools(
            workspace_root,
            runtime_context=runtime_context,
        ),
        *build_front_system_tools(runtime_context=runtime_context),
    ]


__all__ = [
    "CameraTool",
    "DanceTool",
    "DoNothingTool",
    "HeadTrackingTool",
    "MoveHeadTool",
    "PlayEmotionTool",
    "ReachyToolContext",
    "StopDanceTool",
    "StopEmotionTool",
    "build_front_system_tools",
    "build_kernel_system_tools",
    "build_system_tools",
]
