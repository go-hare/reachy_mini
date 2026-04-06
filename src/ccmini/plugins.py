"""Plugin system — load, register, and manage agent plugins.

Mirrors Claude Code's plugin architecture:
- ``PluginManifest``: declarative plugin metadata
- ``PluginRegistry``: discover, load, enable/disable plugins
- ``PluginLoader``: resolve and instantiate plugins from manifests
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .commands.types import Command, CommandSource
from .hooks import Hook
from .tool import Tool

logger = logging.getLogger(__name__)


@dataclass
class PluginManifest:
    """Declarative plugin descriptor."""
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""

    entry_point: str = ""
    tools: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    requires: list[str] = field(default_factory=list)
    settings_schema: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    manifest_path: str = ""


@dataclass
class LoadedPlugin:
    """A fully resolved and instantiated plugin."""
    manifest: PluginManifest
    tools: list[Tool] = field(default_factory=list)
    hooks: list[Hook] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)
    module: Any = None
    error: str = ""

    @property
    def is_loaded(self) -> bool:
        return self.error == "" and self.module is not None


class PluginRegistry:
    """Discover, load, and manage plugins."""

    def __init__(self, plugin_dirs: list[Path] | None = None) -> None:
        self._plugin_dirs = plugin_dirs or []
        self._plugins: dict[str, LoadedPlugin] = {}

    @property
    def plugins(self) -> dict[str, LoadedPlugin]:
        return dict(self._plugins)

    def discover(self) -> list[PluginManifest]:
        """Scan plugin directories for manifests."""
        manifests: list[PluginManifest] = []
        for d in self._plugin_dirs:
            if not d.is_dir():
                continue
            for manifest_path in d.rglob("plugin.json"):
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    data["_manifest_path"] = str(manifest_path)
                    manifests.append(_parse_manifest(data))
                except Exception as exc:
                    logger.warning("Failed to parse plugin manifest %s: %s", manifest_path, exc)
        return manifests

    def load(self, manifest: PluginManifest) -> LoadedPlugin:
        """Load a plugin from its manifest."""
        if not manifest.enabled:
            loaded = LoadedPlugin(manifest=manifest, error="Plugin disabled")
            self._plugins[manifest.name] = loaded
            return loaded

        try:
            module = None
            if manifest.entry_point:
                module = importlib.import_module(manifest.entry_point)

            tools: list[Tool] = []
            hooks: list[Hook] = []
            commands: list[Command] = []

            if module is not None:
                setup = getattr(module, "setup", None)
                if callable(setup):
                    result = setup()
                    if inspect.isawaitable(result):
                        raise RuntimeError("Async plugin setup() is not supported in the sync plugin loader.")
                    if isinstance(result, dict):
                        tools = result.get("tools", [])
                        hooks = result.get("hooks", [])
                        commands = result.get("commands", [])

                if not tools and manifest.tools:
                    for tool_name in manifest.tools:
                        tool_cls = getattr(module, tool_name, None)
                        if tool_cls is not None:
                            try:
                                instance = tool_cls() if callable(tool_cls) else tool_cls
                                if isinstance(instance, Tool):
                                    tools.append(instance)
                            except Exception as exc:
                                logger.warning("Failed to instantiate tool %s: %s", tool_name, exc)

                if not hooks and manifest.hooks:
                    for hook_name in manifest.hooks:
                        hook_cls = getattr(module, hook_name, None)
                        if hook_cls is not None:
                            try:
                                instance = hook_cls() if callable(hook_cls) else hook_cls
                                if isinstance(instance, Hook):
                                    hooks.append(instance)
                            except Exception as exc:
                                logger.warning("Failed to instantiate hook %s: %s", hook_name, exc)

                if not commands and manifest.commands:
                    for command_name in manifest.commands:
                        command_value = getattr(module, command_name, None)
                        if command_value is not None:
                            try:
                                commands.append(_normalize_plugin_command(command_value, manifest.name))
                            except Exception as exc:
                                logger.warning("Failed to register plugin command %s: %s", command_name, exc)

            loaded = LoadedPlugin(
                manifest=manifest,
                tools=tools,
                hooks=hooks,
                commands=[_normalize_plugin_command(command, manifest.name) for command in commands],
                module=module,
            )
        except Exception as exc:
            loaded = LoadedPlugin(manifest=manifest, error=str(exc))
            logger.error("Failed to load plugin '%s': %s", manifest.name, exc)

        self._plugins[manifest.name] = loaded
        return loaded

    def load_all(self) -> list[LoadedPlugin]:
        """Discover and load all plugins."""
        manifests = self.discover()
        return [self.load(m) for m in manifests]

    def get_all_tools(self) -> list[Tool]:
        """Aggregate tools from all loaded plugins."""
        tools: list[Tool] = []
        for p in self._plugins.values():
            if p.is_loaded:
                tools.extend(p.tools)
        return tools

    def get_all_hooks(self) -> list[Hook]:
        """Aggregate hooks from all loaded plugins."""
        hooks: list[Hook] = []
        for p in self._plugins.values():
            if p.is_loaded:
                hooks.extend(p.hooks)
        return hooks

    def get_all_commands(self) -> list[Command]:
        """Aggregate commands from all loaded plugins."""
        commands: list[Command] = []
        for plugin in self._plugins.values():
            if plugin.is_loaded:
                commands.extend(plugin.commands)
        return commands

    def enable(self, name: str) -> bool:
        p = self._plugins.get(name)
        if p:
            p.manifest.enabled = True
            _persist_enabled_flag(p.manifest, True)
            return True
        return False

    def disable(self, name: str) -> bool:
        p = self._plugins.get(name)
        if p:
            p.manifest.enabled = False
            _persist_enabled_flag(p.manifest, False)
            return True
        return False

    def status_summary(self) -> str:
        if not self._plugins:
            return "No plugins loaded."
        lines: list[str] = []
        for name, p in self._plugins.items():
            status = "loaded" if p.is_loaded else f"error: {p.error}"
            tools = len(p.tools)
            hooks = len(p.hooks)
            commands = len(p.commands)
            lines.append(
                f"  {name} v{p.manifest.version}: {status} "
                f"({tools} tools, {hooks} hooks, {commands} commands)"
            )
        return "Plugins:\n" + "\n".join(lines)


def _parse_manifest(data: dict[str, Any]) -> PluginManifest:
    return PluginManifest(
        name=data.get("name", "unknown"),
        version=data.get("version", "0.1.0"),
        description=data.get("description", ""),
        author=data.get("author", ""),
        entry_point=data.get("entry_point", ""),
        tools=data.get("tools", []),
        hooks=data.get("hooks", []),
        commands=data.get("commands", []),
        requires=data.get("requires", []),
        settings_schema=data.get("settings_schema", {}),
        enabled=data.get("enabled", True),
        manifest_path=str(data.get("_manifest_path", "")),
    )


def load_manifest_file(path: Path) -> PluginManifest:
    """Load a plugin manifest from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_manifest_path"] = str(path)
    return _parse_manifest(data)


def discover_plugin_dirs_for_path(cwd: str | Path) -> list[Path]:
    """Discover project-local and global plugin directories."""
    from .paths import mini_agent_home

    current = Path(cwd).resolve()
    dirs: list[Path] = []

    project_dir = current / ".ccmini" / "plugins"
    if project_dir.is_dir():
        dirs.append(project_dir)
    legacy_project_dir = current / ".mini_agent" / "plugins"
    if legacy_project_dir.is_dir():
        dirs.append(legacy_project_dir)

    global_dir = mini_agent_home() / "plugins"
    if global_dir.is_dir():
        dirs.append(global_dir)

    return dirs


def _normalize_plugin_command(command: Command, plugin_name: str) -> Command:
    command.source = CommandSource.PLUGIN
    command.loaded_from = CommandSource.PLUGIN
    command.plugin_name = plugin_name
    return command


def _persist_enabled_flag(manifest: PluginManifest, enabled: bool) -> None:
    if not manifest.manifest_path:
        return
    path = Path(manifest.manifest_path)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    payload["enabled"] = enabled
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
