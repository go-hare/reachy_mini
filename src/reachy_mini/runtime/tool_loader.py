"""Runtime tool loading for resident app profiles."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from reachy_mini.runtime.profile_loader import ProfileBundle
from reachy_mini.runtime.tools import (
    ReachyToolContext,
    build_front_system_tools,
    build_kernel_system_tools,
)


@dataclass(frozen=True)
class RuntimeToolBundle:
    """Resolved runtime tools for one profile-backed app."""

    workspace_root: Path
    kernel_system_tools: list[Any]
    front_tools: list[Any]
    profile_tools: list[Any]

    @property
    def all_tools(self) -> list[Any]:
        """Legacy alias for the kernel-visible tool set."""
        return self.kernel_tools

    @property
    def system_tool_names(self) -> list[str]:
        """Legacy built-in tool names spanning kernel and front planes."""
        return [
            *self.kernel_system_tool_names,
            *self.front_tool_names,
        ]

    @property
    def system_tools(self) -> list[Any]:
        """Legacy built-in tool list spanning kernel and front planes."""
        return [*self.kernel_system_tools, *self.front_tools]

    @property
    def kernel_tools(self) -> list[Any]:
        return [*self.kernel_system_tools, *self.profile_tools]

    @property
    def kernel_system_tool_names(self) -> list[str]:
        return [str(getattr(tool, "name", "") or "").strip() for tool in self.kernel_system_tools]

    @property
    def front_tool_names(self) -> list[str]:
        return [str(getattr(tool, "name", "") or "").strip() for tool in self.front_tools]

    @property
    def profile_tool_names(self) -> list[str]:
        return [str(getattr(tool, "name", "") or "").strip() for tool in self.profile_tools]


def build_runtime_tool_bundle(
    profile: ProfileBundle,
    *,
    runtime_context: ReachyToolContext | None = None,
) -> RuntimeToolBundle:
    """Build the separated runtime tool planes for one profile."""
    workspace_root = profile.root.parent.resolve()
    kernel_system_tools = build_kernel_system_tools(
        workspace_root,
        runtime_context=runtime_context,
    )
    front_tools = build_front_system_tools(
        runtime_context=runtime_context,
    )
    profile_tools = load_profile_tools(
        tools_dir=profile.tools_dir,
        workspace_root=workspace_root,
        profile_root=profile.root,
    )
    return RuntimeToolBundle(
        workspace_root=workspace_root,
        kernel_system_tools=kernel_system_tools,
        front_tools=front_tools,
        profile_tools=profile_tools,
    )


def load_profile_tools(
    *,
    tools_dir: Path,
    workspace_root: Path,
    profile_root: Path,
) -> list[Any]:
    """Load user-defined tools from ``profiles/<name>/profiles/tools``."""
    if not tools_dir.exists():
        return []

    loaded: list[Any] = []
    for tool_file in sorted(tools_dir.rglob("*.py")):
        if tool_file.name.startswith("_"):
            continue
        loaded.extend(
            _load_tools_from_module(
                tool_file=tool_file,
                workspace_root=workspace_root,
                profile_root=profile_root,
                tools_dir=tools_dir,
            )
        )
    return loaded


def _load_tools_from_module(
    *,
    tool_file: Path,
    workspace_root: Path,
    profile_root: Path,
    tools_dir: Path,
) -> list[Any]:
    module = _load_module(tool_file)
    built = _extract_module_tools(
        module,
        workspace_root=workspace_root,
        profile_root=profile_root,
        tools_dir=tools_dir,
    )
    if built is None:
        return []
    if isinstance(built, (list, tuple)):
        return list(built)
    return [built]


def _load_module(tool_file: Path) -> ModuleType:
    module_name = f"reachy_mini_profile_tool_{tool_file.stem}_{abs(hash(tool_file.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, tool_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load tool module: {tool_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_module_tools(
    module: ModuleType,
    *,
    workspace_root: Path,
    profile_root: Path,
    tools_dir: Path,
) -> Any:
    builder = getattr(module, "build_tools", None)
    if callable(builder):
        return builder(
            workspace_root=workspace_root,
            profile_root=profile_root,
            tools_dir=tools_dir,
        )
    if hasattr(module, "TOOLS"):
        return getattr(module, "TOOLS")
    if hasattr(module, "TOOL"):
        return getattr(module, "TOOL")
    return None
