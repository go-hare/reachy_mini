"""Load third-party extensions from setuptools entry points.

Any Python package may register:

- ``ccmini.tools`` — callables returning :class:`~ccmini.tool.Tool` or a list of tools
- ``ccmini.hooks`` — callables returning :class:`~ccmini.hooks.Hook` or a list of hooks

Example (in the plugin's ``pyproject.toml``)::

    [project.entry-points.\"ccmini.tools\"]
    my_robot_gripper = \"my_pkg.ccmini_ext:register_tools\"

    # my_pkg/ccmini_ext.py
    def register_tools():
        from my_pkg.tools import GripperTool
        return [GripperTool()]

Entry points are loaded after directory-based :class:`~ccmini.plugins.PluginRegistry`
plugins; failures are logged per entry and do not abort agent startup.
"""

from __future__ import annotations

import logging
from typing import Any

from .hooks import Hook
from .tool import Tool

logger = logging.getLogger(__name__)

GROUP_TOOLS = "ccmini.tools"
GROUP_HOOKS = "ccmini.hooks"

__all__ = [
    "GROUP_HOOKS",
    "GROUP_TOOLS",
    "load_hooks_from_entry_points",
    "load_tools_from_entry_points",
]


def _entry_points_for_group(group: str) -> list[Any]:
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        from importlib_metadata import entry_points  # type: ignore[no-redef]

    eps_factory = entry_points
    try:
        selected = eps_factory(group=group)
    except TypeError:
        all_eps = eps_factory()
        select = getattr(all_eps, "select", None)
        if callable(select):
            selected = select(group=group)
        else:
            selected = all_eps.get(group, [])  # type: ignore[union-attr]
    return list(selected)


def _invoke_entry(ep: Any) -> Any:
    target = ep.load()
    if callable(target):
        return target()
    return target


def _normalize_tools(result: Any, *, ep_name: str) -> list[Tool]:
    if result is None:
        return []
    if isinstance(result, Tool):
        return [result]
    if isinstance(result, (list, tuple)):
        out: list[Tool] = []
        for item in result:
            if isinstance(item, Tool):
                out.append(item)
            else:
                logger.warning(
                    "ccmini.tools entry %r: skipping non-Tool in sequence: %s",
                    ep_name,
                    type(item).__name__,
                )
        return out
    logger.warning(
        "ccmini.tools entry %r: expected Tool or list[Tool], got %s",
        ep_name,
        type(result).__name__,
    )
    return []


def _normalize_hooks(result: Any, *, ep_name: str) -> list[Hook]:
    if result is None:
        return []
    if isinstance(result, Hook):
        return [result]
    if isinstance(result, (list, tuple)):
        out: list[Hook] = []
        for item in result:
            if isinstance(item, Hook):
                out.append(item)
            else:
                logger.warning(
                    "ccmini.hooks entry %r: skipping non-Hook in sequence: %s",
                    ep_name,
                    type(item).__name__,
                )
        return out
    logger.warning(
        "ccmini.hooks entry %r: expected Hook or list[Hook], got %s",
        ep_name,
        type(result).__name__,
    )
    return []


def load_tools_from_entry_points(*, group: str = GROUP_TOOLS) -> list[Tool]:
    """Load tools from setuptools entry points (default group ``ccmini.tools``)."""
    tools: list[Tool] = []
    for ep in _entry_points_for_group(group):
        try:
            result = _invoke_entry(ep)
        except Exception:
            logger.warning("ccmini.tools entry %r failed", ep.name, exc_info=True)
            continue
        for t in _normalize_tools(result, ep_name=ep.name):
            tools.append(t)
    return tools


def load_hooks_from_entry_points(*, group: str = GROUP_HOOKS) -> list[Hook]:
    """Load hooks from setuptools entry points (default group ``ccmini.hooks``)."""
    hooks: list[Hook] = []
    for ep in _entry_points_for_group(group):
        try:
            result = _invoke_entry(ep)
        except Exception:
            logger.warning("ccmini.hooks entry %r failed", ep.name, exc_info=True)
            continue
        for h in _normalize_hooks(result, ep_name=ep.name):
            hooks.append(h)
    return hooks
