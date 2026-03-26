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


def build_system_tools(
    workspace_root: Path,
    *,
    runtime_context: ReachyToolContext | None = None,
) -> list[object]:
    """Build the default system tool instances for one app workspace."""
    workspace = workspace_root.resolve()
    context = runtime_context or ReachyToolContext()
    return [
        ReadFileTool(workspace=workspace, allowed_dir=workspace),
        WriteFileTool(workspace=workspace, allowed_dir=workspace),
        EditFileTool(workspace=workspace, allowed_dir=workspace),
        ListDirTool(workspace=workspace, allowed_dir=workspace),
        SearchFilesTool(workspace=workspace, allowed_dir=workspace),
        InsertLinesTool(workspace=workspace, allowed_dir=workspace),
        DeleteLinesTool(workspace=workspace, allowed_dir=workspace),
        ReplaceLinesTool(workspace=workspace, allowed_dir=workspace),
        MoveHeadTool(context=context),
        DoNothingTool(context=context),
        HeadTrackingTool(context=context),
        CameraTool(context=context),
        PlayEmotionTool(context=context),
        DanceTool(context=context),
        StopEmotionTool(context=context),
        StopDanceTool(context=context),
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
    "build_system_tools",
]
