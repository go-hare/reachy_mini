"""Built-in slash commands."""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import SlashCommand, CommandRegistry, command_from_bundled_skill
from .types import Command, CommandSource, CommandType

if TYPE_CHECKING:
    from ..agent import Agent


def _refresh_permission_runtime(agent: Agent) -> None:
    """Reload the runtime permission checker from persisted config."""
    from ..config import load_config
    from ..permissions import build_permission_checker, PermissionMode

    cfg = load_config()
    agent._permission_checker = build_permission_checker(
        mode=cfg.permission_mode,
        raw_rules=cfg.permission_rules,
        classifier_provider=agent.provider if cfg.permission_mode == PermissionMode.AUTO.value else None,
        project_dir=os.getcwd(),
    )


def _parse_on_off(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"on", "true", "1", "yes", "enabled"}:
        return True
    if normalized in {"off", "false", "0", "no", "disabled"}:
        return False
    return None


def _detect_terminal_environment() -> tuple[str, str]:
    """Best-effort terminal/editor detection for setup guidance."""
    term_program = os.environ.get("TERM_PROGRAM", "").strip()
    wt_session = os.environ.get("WT_SESSION", "").strip()
    vscode_pid = os.environ.get("VSCODE_IPC_HOOK_CLI", "").strip() or os.environ.get("VSCODE_GIT_IPC_HANDLE", "").strip()
    cursor_trace = os.environ.get("CURSOR_TRACE_ID", "").strip()
    windsurf_trace = os.environ.get("WINDSURF_TRACE_ID", "").strip()
    zed = os.environ.get("ZED_TERM", "").strip()
    term = os.environ.get("TERM", "").strip()

    if term_program == "Apple_Terminal":
        return "apple_terminal", "Apple Terminal"
    if term_program == "vscode" or vscode_pid:
        return "vscode", "VS Code Terminal"
    if term_program.lower() == "wezterm":
        return "wezterm", "WezTerm"
    if term_program.lower() == "warpterminal":
        return "warp", "Warp"
    if cursor_trace or term_program.lower() == "cursor":
        return "cursor", "Cursor Terminal"
    if windsurf_trace or term_program.lower() == "windsurf":
        return "windsurf", "Windsurf Terminal"
    if zed:
        return "zed", "Zed Terminal"
    if wt_session:
        return "windows_terminal", "Windows Terminal"
    if "ghostty" in term.lower():
        return "ghostty", "Ghostty"
    if "kitty" in term.lower():
        return "kitty", "Kitty"
    if "alacritty" in term.lower():
        return "alacritty", "Alacritty"
    return "unknown", Path(os.environ.get("SHELL", os.environ.get("COMSPEC", ""))).name or "unknown terminal"


def _terminal_supports_native_shift_enter(terminal_id: str) -> bool:
    return terminal_id in {"wezterm", "warp", "ghostty", "kitty"}


def _save_multiline_preference(value: str) -> Path:
    from ..config import save_global_config
    from ..keybindings import get_registry

    normalized = value.strip().lower()
    if normalized not in {"shift+enter", "alt+enter"}:
        raise ValueError(f"Unsupported multiline key: {value}")

    registry = get_registry()
    registry.unregister("Shift+Enter")
    registry.unregister("Alt+Enter")
    registry.register(
        "Shift+Enter" if normalized == "shift+enter" else "Alt+Enter",
        "multiline",
        description="Multi-line input mode",
    )
    registry.save_to_config()
    return save_global_config({"multiline_key": normalized})


def _render_terminal_setup_help() -> str:
    return (
        "Usage:\n"
        "  /terminal-setup\n"
        "  /terminal-setup status\n"
        "  /terminal-setup auto\n"
        "  /terminal-setup shift-enter\n"
        "  /terminal-setup alt-enter\n"
        "  /terminal-setup reset"
    )


def _describe_editor_source() -> str:
    """Describe which editor configuration will be used, if any."""
    visual = os.environ.get("VISUAL", "").strip()
    if visual:
        return f'Using $VISUAL="{visual}".'

    editor = os.environ.get("EDITOR", "").strip()
    if editor:
        return f'Using $EDITOR="{editor}".'

    return ""


def _open_file_in_editor(path: Path) -> None:
    """Open a file in the configured editor or platform default app."""
    visual = os.environ.get("VISUAL", "").strip()
    editor = os.environ.get("EDITOR", "").strip()
    editor_cmd = visual or editor

    if editor_cmd:
        cmd = [*shlex.split(editor_cmd, posix=os.name != "nt"), str(path)]
        subprocess.run(cmd, check=True)
        return

    if sys.platform == "win32":
        os.startfile(str(path))
        return

    opener = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.run(opener, check=True)


def _resolve_memory_file(memory_dir: Path, raw_args: str) -> Path:
    """Resolve the requested memory file under the configured memory dir."""
    requested = raw_args.strip() or "MEMORY.md"
    relative = Path(requested)

    if relative.is_absolute():
        raise ValueError("Memory file path must be relative to the memory directory.")

    target = (memory_dir / relative).resolve()
    memory_root = memory_dir.resolve()

    if target != memory_root and memory_root not in target.parents:
        raise ValueError("Memory file path must stay within the memory directory.")

    return target


def _display_path_from_cwd(path: str | Path) -> str:
    """Render a path relative to the current working directory when possible."""
    raw = str(path)
    try:
        return os.path.relpath(raw, os.getcwd())
    except ValueError:
        return raw


def _sanitize_task_list_id(value: str) -> str:
    sanitized = "".join(
        ch if ch.isalnum() or ch in "-_" else "-"
        for ch in value.strip()
    )
    return sanitized or "tasklist"


def _resolve_task_list_id_for_agent(agent: Agent) -> str:
    explicit_task_list_id = os.environ.get("CLAUDE_CODE_TASK_LIST_ID", "").strip()
    if explicit_task_list_id:
        return _sanitize_task_list_id(explicit_task_list_id)

    team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME", "").strip()
    if team_name:
        return _sanitize_task_list_id(team_name)

    conversation_id = str(getattr(agent, "conversation_id", "") or "").strip()
    if conversation_id:
        return _sanitize_task_list_id(conversation_id)

    return "tasklist"


def _resolve_ccmini_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("ccmini")
        except PackageNotFoundError:
            return "dev"
    except Exception:
        return "dev"


class ContextCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "context"

    @property
    def description(self) -> str:
        return "Show current context/token window usage"

    async def execute(self, args: str, agent: Agent) -> str:
        tracker = agent.usage_tracker
        summary = tracker.summary()
        total_input = summary.get("total_input_tokens", 0)
        total_output = summary.get("total_output_tokens", 0)
        total = total_input + total_output
        max_tokens = getattr(getattr(agent.provider, "config", None), "max_tokens", 200_000) or 200_000
        used_pct = (total / max_tokens * 100) if max_tokens else 0.0
        return (
            "Context usage:\n"
            f"  input_tokens: {total_input:,}\n"
            f"  output_tokens: {total_output:,}\n"
            f"  total_tokens: {total:,}\n"
            f"  configured_max_tokens: {max_tokens:,}\n"
            f"  estimated_usage: {used_pct:.1f}%"
        )


class ThemeCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "theme"

    @property
    def description(self) -> str:
        return "Show or change saved theme preference"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..config import BUILTIN_THEME_NAMES, load_config, save_global_config

        value = args.strip().lower()
        themes = list(BUILTIN_THEME_NAMES)
        cfg = load_config()

        if not value:
            return f"Current theme: {cfg.theme}\nAvailable themes: auto, {', '.join(themes)}"
        if value not in {"auto", *themes}:
            return f"Unknown theme '{value}'. Available: auto, {', '.join(themes)}"

        save_global_config({"theme": value})
        return f"Theme set to: {value}"


class OutputStyleCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "output-style"

    @property
    def hidden(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return "Deprecated alias for output-style configuration"

    async def execute(self, args: str, agent: Agent) -> str:
        return (
            "/output-style has been deprecated. Use /config to change your "
            "output style, or set it in your settings file. Changes take "
            "effect on the next session."
        )


class CompactCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "compact"

    @property
    def description(self) -> str:
        return "Manually trigger context compaction"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..engine.compact import build_post_compact_messages, collapse_tool_sequences, reactive_compact
        before = len(agent.messages)
        collapsed = collapse_tool_sequences(agent._messages)
        if len(collapsed) < before:
            agent._messages = collapsed
            return f"Collapsed {before - len(collapsed)} tool sequences."
        compaction = await reactive_compact(agent._messages, agent.provider)
        agent._messages = build_post_compact_messages(compaction)
        after = len(agent._messages)
        return f"Compacted: {before} -> {after} messages."


class ClearCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "clear"

    @property
    def aliases(self) -> list[str]:
        return ["reset", "new"]

    @property
    def description(self) -> str:
        return "Clear conversation history"

    async def execute(self, args: str, agent: Agent) -> str:
        count = len(agent._messages)
        agent._messages.clear()
        return f"Cleared {count} messages."


class MemoryCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "Open memory files for editing"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..services.memdir import get_memory_dir

        memory_dir = Path(get_memory_dir()).expanduser()

        try:
            target = _resolve_memory_file(memory_dir, args)
        except ValueError as exc:
            return f"Error opening memory file: {exc}"

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch(exist_ok=True)
            _open_file_in_editor(target)
        except Exception as exc:
            return f"Error opening memory file: {exc}"

        try:
            display_path = target.relative_to(memory_dir.resolve())
        except ValueError:
            display_path = target

        editor_info = _describe_editor_source()
        editor_hint = (
            f"> {editor_info} To change editor, set $EDITOR or $VISUAL environment variable."
            if editor_info
            else "> To use a different editor, set the $EDITOR or $VISUAL environment variable."
        )
        return (
            f"Opened memory file at {display_path}\n"
            f"Absolute path: {target}\n\n"
            f"{editor_hint}"
        )


class FilesCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "files"

    @property
    def enabled(self) -> bool:
        return os.environ.get("USER_TYPE", "") == "ant"

    @property
    def description(self) -> str:
        return "Show files currently in context"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..tools.file_read import get_read_file_state

        files = sorted(get_read_file_state())
        if not files:
            return "No files in context"

        display = [_display_path_from_cwd(path) for path in files]
        return "Files in context:\n" + "\n".join(display)


class KeybindingsCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "keybindings"

    @property
    def description(self) -> str:
        return "Create or open the keybindings config in your editor"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..keybindings import generate_keybindings_template, get_keybindings_path

        if args.strip():
            return "Usage: /keybindings"

        path = get_keybindings_path()
        created = False

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(generate_keybindings_template(), encoding="utf-8")
                created = True
        except Exception as exc:
            return f"Could not prepare keybindings file {path}: {exc}"

        try:
            _open_file_in_editor(path)
        except Exception as exc:
            prefix = (
                f"Created {path} with template."
                if created
                else f"Opened {path}."
            )
            return f"{prefix} Could not open in editor: {exc}"

        if created:
            return f"Created {path} with template. Opened in your editor."
        return f"Opened {path} in your editor."


class HelpCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "help"

    @property
    def description(self) -> str:
        return "Show available commands"

    async def execute(self, args: str, agent: Agent) -> str:
        if not hasattr(agent, "_command_registry") or agent._command_registry is None:
            return "No commands available."
        slash_cmds = agent._command_registry.list_commands()
        slash_lines = [f"  /{c.name} - {c.description}" for c in slash_cmds]

        bundled = [
            c for c in agent._command_registry.get_all_commands()
            if c.user_invocable and c.source.name.lower() == "bundled"
        ]
        bundled_lines = [f"  {c.name} - {c.description}" for c in bundled]

        sections = [
            "Slash commands:",
            *slash_lines,
        ]
        if bundled_lines:
            sections.extend([
                "",
                "Bundled skills:",
                *bundled_lines,
            ])
        return "\n".join(sections)


class BriefCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "brief"

    @property
    def description(self) -> str:
        return "Control brief mode, view mode, and response verbosity"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..config import save_global_config
        from ..kairos import (
            BriefLevel,
            ViewMode,
            get_brief_level,
            get_message_history,
            get_view_mode,
            is_brief_enabled,
            is_brief_entitled,
            set_brief_level,
            set_view_mode,
            toggle_brief,
        )
        from ..kairos.brief import clear_message_history

        parts = args.strip().split()
        action = parts[0].lower() if parts else "status"

        def _status() -> str:
            level = get_brief_level()
            view = get_view_mode()
            history = get_message_history(limit=5)
            lines = [
                "Brief:",
                f"  enabled: {'yes' if is_brief_enabled() else 'no'}",
                f"  entitled: {'yes' if is_brief_entitled() else 'no'}",
                f"  level: {level.value}",
                f"  view: {view.value}",
                f"  message_history: {len(get_message_history(limit=1000))}",
            ]
            if history:
                lines.append("  recent_messages:")
                for message in history[-5:]:
                    title = f"[{message.title}] " if message.title else ""
                    preview = message.content.replace("\n", " ")[:80]
                    lines.append(f"    - {title}{preview}")
            lines.extend([
                "",
                "Usage:",
                "  /brief",
                "  /brief on|off|toggle",
                "  /brief chat|transcript",
                "  /brief level <normal|brief|minimal|silent>",
                "  /brief history [count]",
                "  /brief clear",
            ])
            return "\n".join(lines)

        if action in {"", "status"}:
            return _status()

        if action in {"on", "off", "toggle"}:
            if action == "toggle":
                enabled = toggle_brief()
            else:
                enabled = action == "on"
                set_brief_level(BriefLevel.BRIEF if enabled else BriefLevel.NORMAL)
            if enabled:
                with contextlib.suppress(Exception):
                    agent._install_kairos_prompt_sections()
            save_global_config({"kairos_brief_enabled": enabled})
            return f"Brief mode {'enabled' if enabled else 'disabled'}."

        if action in {"chat", "transcript"}:
            mode = ViewMode.CHAT if action == "chat" else ViewMode.TRANSCRIPT
            set_view_mode(mode)
            return f"Brief view mode set to: {mode.value}"

        if action == "level":
            if len(parts) < 2:
                return "Usage: /brief level <normal|brief|minimal|silent>"
            try:
                level = BriefLevel(parts[1].lower())
            except ValueError:
                return "Usage: /brief level <normal|brief|minimal|silent>"
            set_brief_level(level)
            if level != BriefLevel.NORMAL:
                with contextlib.suppress(Exception):
                    agent._install_kairos_prompt_sections()
            save_global_config({"kairos_brief_enabled": level != BriefLevel.NORMAL})
            return f"Brief level set to: {level.value}"

        if action == "history":
            count = 10
            if len(parts) > 1 and parts[1].isdigit():
                count = max(1, int(parts[1]))
            history = get_message_history(limit=count)
            if not history:
                return "Brief message history is empty."
            lines = [f"Brief history ({len(history)}):"]
            for message in history:
                title = f"[{message.title}] " if message.title else ""
                lines.append(f"  - {title}{message.content.replace(chr(10), ' ')}")
            return "\n".join(lines)

        if action == "clear":
            clear_message_history()
            return "Cleared brief message history."

        return "Usage: /brief [status|on|off|toggle|chat|transcript|level <value>|history [count]|clear]"


class RenameCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "rename"

    @property
    def description(self) -> str:
        return "Rename the current conversation"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..tools.list_peers import update_session_name

        new_name = args.strip()
        current_name = getattr(agent, "_session_name", "").strip()

        if not new_name:
            if current_name:
                return f"Current conversation name: {current_name}"
            return f"Conversation has no custom name yet. Session ID: {agent.conversation_id}"

        setattr(agent, "_session_name", new_name)
        update_session_name(agent.conversation_id, new_name)
        store = getattr(agent, "_session_store", None)
        if store is not None:
            try:
                store.set_title(agent.conversation_id, new_name)
            except Exception:
                pass
        return f"Renamed conversation to: {new_name}"


class PlanCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "plan"

    @property
    def description(self) -> str:
        return "Enable plan mode or view the current session plan"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..tools.plan_mode import (
            ENTER_PLAN_INSTRUCTIONS,
            _save_plan_to_file,
            enter_plan_mode,
            get_plan_state,
            is_plan_mode_active,
        )

        sub = args.strip()
        state = get_plan_state()

        if sub.lower() == "open":
            if not state.plan_text:
                return "No saved plan yet. Use /plan first to enter plan mode."
            path = _save_plan_to_file(state.plan_text)
            try:
                _open_file_in_editor(path)
            except Exception as exc:
                return f"Failed to open plan in editor: {exc}"
            return f"Opened plan in editor: {path}"

        if not is_plan_mode_active():
            enter_plan_mode(
                permission_checker=getattr(agent, "_permission_checker", None),
            )
            if sub:
                return (
                    "Enabled plan mode.\n\n"
                    f"Planning focus: {sub}\n\n"
                    f"{ENTER_PLAN_INSTRUCTIONS}"
                )
            return f"Enabled plan mode.\n\n{ENTER_PLAN_INSTRUCTIONS}"

        if state.plan_text:
            path = _save_plan_to_file(state.plan_text)
            return f"Current plan:\n\n{state.plan_text}\n\nSaved at: {path}"

        return "Plan mode is active, but no plan has been written yet."


class ConfigCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "config"

    @property
    def aliases(self) -> list[str]:
        return ["settings"]

    @property
    def description(self) -> str:
        return "Show or update persistent CLI configuration"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..config import BUILTIN_THEME_NAMES, load_config, save_global_config
        from ..kairos import GateConfig, set_gate_config
        from ..output_styles import OutputStyle
        from ..permissions import PermissionMode

        parts = args.strip().split()
        cfg = load_config()
        provider_names = ["anthropic", "openai", "compatible", "ollama", "vllm", "deepseek", "mock"]
        output_styles = [style.value for style in OutputStyle]

        def render_overview() -> str:
            return (
                "Config:\n"
                f"  provider: {cfg.provider}\n"
                f"  model: {cfg.model or '(default)'}\n"
                f"  base_url: {cfg.base_url or '(default)'}\n"
                f"  api_key: {'set' if cfg.api_key else '(unset)'}\n"
                f"  ccmini_host: {cfg.ccmini_host}\n"
                f"  ccmini_port: {cfg.ccmini_port}\n"
                f"  ccmini_auth_token: {'set' if cfg.ccmini_auth_token else '(unset)'}\n"
                f"  theme: {cfg.theme}\n"
                f"  output_style: {cfg.output_style}\n"
                f"  multiline_key: {cfg.multiline_key}\n"
                f"  permission_mode: {cfg.permission_mode}\n"
                f"  permission_rules: {len(cfg.permission_rules)}\n"
                f"  statusline_enabled: {cfg.statusline_enabled}\n"
                f"  coordinator_enabled: {cfg.coordinator_enabled}\n"
                f"  kairos_enabled: {cfg.kairos_enabled}\n"
                f"  kairos_brief_enabled: {cfg.kairos_brief_enabled}\n"
                f"  kairos_cron_enabled: {cfg.kairos_cron_enabled}\n"
                f"  kairos_channels_enabled: {cfg.kairos_channels_enabled}\n"
                f"  kairos_dream_enabled: {cfg.kairos_dream_enabled}\n"
                "\n"
                "  built_in_agents:\n"
                f"    explore_plan: {cfg.builtin_explore_plan_agents_enabled}\n"
                f"    verification: {cfg.builtin_verification_agent_enabled}\n"
                f"    statusline_guide: {cfg.builtin_statusline_guide_agent_enabled}\n"
                f"    claude_docs_guide: {cfg.builtin_claude_docs_guide_agent_enabled}\n"
                f"  session_persistence: {cfg.session_persistence}\n"
                f"  tools_enabled: {cfg.tools_enabled}\n"
                f"  buddy_enabled: {cfg.buddy_enabled}\n"
                "\n"
                "Usage:\n"
                "  /config\n"
                "  /config get <key>\n"
                "  /config unset <key>\n"
                "  /config theme [name]\n"
                "  /config output-style [markdown|json|plain|compact|structured]\n"
                "  /config multiline-key [shift+enter|alt+enter]\n"
                "  /config provider [name]\n"
                "  /config model [name]\n"
                "  /config base-url [url]\n"
                "  /config api-key [key]\n"
                "  /config ccmini-host [host]\n"
                "  /config ccmini-port [port]\n"
                "  /config ccmini-auth-token [token]\n"
                "  /config permission-mode [default|accept_edits|bypass|plan|auto]\n"
                "  /config statusline [on|off]\n"
                "  /config coordinator [on|off]\n"
                "  /config kairos [on|off]\n"
                "  /config kairos-brief [on|off]\n"
                "  /config kairos-cron [on|off]\n"
                "  /config kairos-channels [on|off]\n"
                "  /config kairos-dream [on|off]\n"
                "  /config get builtin_explore_plan_agents_enabled\n"
                "  /config get builtin_verification_agent_enabled\n"
                "  /config get builtin_statusline_guide_agent_enabled\n"
                "  /config get builtin_claude_docs_guide_agent_enabled\n"
                "  /config agent-explore-plan [on|off]\n"
                "  /config agent-verification [on|off]\n"
                "  /config session-persistence [on|off]\n"
                "  /config tools [on|off]\n"
                "  /config buddy [on|off]\n"
            )

        if not parts:
            return render_overview()

        key = parts[0].lower()
        value = " ".join(parts[1:]).strip()

        if key == "get":
            if len(parts) < 2:
                return "Usage: /config get <key>"
            requested = parts[1].replace("-", "_")
            if not hasattr(cfg, requested):
                return f"Unknown config key '{parts[1]}'."
            current = getattr(cfg, requested)
            if requested == "api_key":
                current = "set" if current else "(unset)"
            return f"{requested}: {current}"

        if key == "unset":
            if len(parts) < 2:
                return "Usage: /config unset <key>"
            target = parts[1].lower()
            resettable = {
                "model": "",
                "base-url": "",
                "base_url": "",
                "api-key": "",
                "api_key": "",
                "ccmini-auth-token": "",
                "ccmini_auth_token": "",
            }
            if target not in resettable:
                return "Unset supports: model, base-url, api-key, ccmini-auth-token"
            normalized = target.replace("-", "_")
            save_global_config({normalized: resettable[target]})
            return f"Cleared config key: {normalized}"

        if key == "theme":
            if not value:
                return f"Current theme: {cfg.theme}\nAvailable themes: auto, {', '.join(BUILTIN_THEME_NAMES)}"
            if value not in {"auto", *BUILTIN_THEME_NAMES}:
                return f"Unknown theme '{value}'. Available: auto, {', '.join(BUILTIN_THEME_NAMES)}"
            save_global_config({"theme": value})
            return f"Saved theme: {value}"

        if key in {"output-style", "output_style"}:
            if not value:
                return (
                    f"Current output style: {cfg.output_style}\n"
                    f"Available output styles: {', '.join(output_styles)}"
                )
            if value not in output_styles:
                return (
                    f"Unknown output style '{value}'. "
                    f"Available: {', '.join(output_styles)}"
                )
            save_global_config({"output_style": value})
            setattr(agent, "_output_style", value)
            return f"Saved output style: {value} (used on next session start)"

        if key in {"multiline-key", "multiline_key"}:
            if not value:
                return f"Current multiline key: {cfg.multiline_key}\nAvailable: shift+enter, alt+enter"
            if value not in {"shift+enter", "alt+enter"}:
                return "Usage: /config multiline-key [shift+enter|alt+enter]"
            path = _save_multiline_preference(value)
            setattr(agent, "_multiline_key", value)
            return f"Saved multiline key: {value}\nConfig updated: {path}"

        if key == "provider":
            if not value:
                return f"Current provider: {cfg.provider}\nAvailable providers: {', '.join(provider_names)}"
            if value not in provider_names:
                return f"Unknown provider '{value}'. Available: {', '.join(provider_names)}"
            save_global_config({"provider": value})
            return f"Saved provider: {value} (used on next session start)"

        if key == "model":
            if not value:
                return f"Current model: {cfg.model or '(default)'}"
            save_global_config({"model": value})
            return f"Saved model: {value} (used on next session start)"

        if key in {"base-url", "base_url"}:
            if not value:
                return f"Current base_url: {cfg.base_url or '(default)'}"
            save_global_config({"base_url": value})
            return f"Saved base_url: {value} (used on next session start)"

        if key in {"api-key", "api_key"}:
            if not value:
                return f"Current api_key: {'set' if cfg.api_key else '(unset)'}"
            save_global_config({"api_key": value})
            return "Saved api_key. It will be used on next session start."

        if key in {"ccmini-host", "ccmini_host"}:
            if not value:
                return f"Current ccmini_host: {cfg.ccmini_host}"
            save_global_config({"ccmini_host": value})
            return f"Saved ccmini_host: {value}"

        if key in {"ccmini-port", "ccmini_port"}:
            if not value:
                return f"Current ccmini_port: {cfg.ccmini_port}"
            try:
                port = int(value)
            except ValueError:
                return "Usage: /config ccmini-port [positive integer]"
            if port < 1:
                return "Usage: /config ccmini-port [positive integer]"
            save_global_config({"ccmini_port": port})
            return f"Saved ccmini_port: {port}"

        if key in {"ccmini-auth-token", "ccmini_auth_token"}:
            if not value:
                return (
                    "Current ccmini_auth_token: "
                    f"{'set' if cfg.ccmini_auth_token else '(unset)'}"
                )
            save_global_config({"ccmini_auth_token": value})
            return "Saved ccmini_auth_token. It will be used on next session start."

        if key in {"permission-mode", "permission_mode"}:
            allowed_modes = [mode.value for mode in PermissionMode]
            if not value:
                return (
                    f"Current permission mode: {cfg.permission_mode}\n"
                    f"Available modes: {', '.join(allowed_modes)}"
                )
            if value not in allowed_modes:
                return f"Unknown permission mode '{value}'. Available: {', '.join(allowed_modes)}"
            save_global_config({"permission_mode": value})
            _refresh_permission_runtime(agent)
            return f"Saved permission mode: {value}"

        if key == "statusline":
            if not value:
                return f"Status line is currently {'on' if cfg.statusline_enabled else 'off'}."
            enabled = _parse_on_off(value)
            if enabled is None:
                return "Usage: /config statusline [on|off]"
            save_global_config({"statusline_enabled": enabled})
            setattr(agent, "_statusline_enabled", enabled)
            status_line = getattr(agent, "_status_line", None)
            if status_line is not None:
                if enabled:
                    status_line.show()
                else:
                    status_line.hide()
            return f"Saved status line setting: {'on' if enabled else 'off'}"

        if key == "coordinator":
            if not value:
                return f"Coordinator mode is currently {'on' if cfg.coordinator_enabled else 'off'}."
            enabled = _parse_on_off(value)
            if enabled is None:
                return "Usage: /config coordinator [on|off]"
            save_global_config({"coordinator_enabled": enabled})
            return f"Saved coordinator: {'on' if enabled else 'off'} (used on next session start)"

        kairos_keys = {
            "kairos": "kairos_enabled",
            "kairos-brief": "kairos_brief_enabled",
            "kairos_brief": "kairos_brief_enabled",
            "kairos-cron": "kairos_cron_enabled",
            "kairos_cron": "kairos_cron_enabled",
            "kairos-channels": "kairos_channels_enabled",
            "kairos_channels": "kairos_channels_enabled",
            "kairos-dream": "kairos_dream_enabled",
            "kairos_dream": "kairos_dream_enabled",
        }
        if key in kairos_keys:
            attr = kairos_keys[key]
            label = attr.replace("_enabled", "")
            current = bool(getattr(cfg, attr))
            if not value:
                return f"{label} is currently {'on' if current else 'off'}."
            enabled = _parse_on_off(value)
            if enabled is None:
                return f"Usage: /config {key} [on|off]"
            save_global_config({attr: enabled})
            gate = getattr(agent._config, "kairos_gate_config", None)
            if gate is not None:
                gate_values = {
                    "kairos_enabled": gate.kairos_enabled,
                    "kairos_brief_enabled": gate.brief_enabled,
                    "kairos_cron_enabled": gate.cron_enabled,
                    "kairos_cron_durable": gate.cron_durable,
                    "kairos_channels_enabled": gate.channels_enabled,
                    "kairos_dream_enabled": gate.dream_enabled,
                }
                gate_values[attr] = enabled
                agent._config.kairos_gate_config = GateConfig(
                    kairos_enabled=gate_values["kairos_enabled"],
                    brief_enabled=gate_values["kairos_brief_enabled"],
                    cron_enabled=gate_values["kairos_cron_enabled"],
                    cron_durable=gate_values["kairos_cron_durable"],
                    channels_enabled=gate_values["kairos_channels_enabled"],
                    dream_enabled=gate_values["kairos_dream_enabled"],
                )
                set_gate_config(agent._config.kairos_gate_config)
            return f"Saved {label}: {'on' if enabled else 'off'} (used on next session start)"

        agent_gate_keys = {
            "agent-explore-plan": "builtin_explore_plan_agents_enabled",
            "agent_explore_plan": "builtin_explore_plan_agents_enabled",
            "agent-verification": "builtin_verification_agent_enabled",
            "agent_verification": "builtin_verification_agent_enabled",
            "agent-statusline-guide": "builtin_statusline_guide_agent_enabled",
            "agent_statusline_guide": "builtin_statusline_guide_agent_enabled",
            "agent-claude-docs-guide": "builtin_claude_docs_guide_agent_enabled",
            "agent_claude_docs_guide": "builtin_claude_docs_guide_agent_enabled",
        }
        if key in agent_gate_keys:
            attr = agent_gate_keys[key]
            current = bool(getattr(cfg, attr))
            if not value:
                return f"{attr} is currently {'on' if current else 'off'}."
            enabled = _parse_on_off(value)
            if enabled is None:
                return f"Usage: /config {key} [on|off]"
            save_global_config({attr: enabled})
            return f"Saved {attr}: {'on' if enabled else 'off'} (used on next session start)"

        if key in {"session-persistence", "session_persistence"}:
            if not value:
                return f"Session persistence is currently {'on' if cfg.session_persistence else 'off'}."
            enabled = _parse_on_off(value)
            if enabled is None:
                return "Usage: /config session-persistence [on|off]"
            save_global_config({"session_persistence": enabled})
            return f"Saved session persistence: {'on' if enabled else 'off'} (used on next session start)"

        if key == "tools":
            if not value:
                return f"Tools are currently {'on' if cfg.tools_enabled else 'off'}."
            enabled = _parse_on_off(value)
            if enabled is None:
                return "Usage: /config tools [on|off]"
            save_global_config({"tools_enabled": enabled})
            return f"Saved tools setting: {'on' if enabled else 'off'} (used on next session start)"

        if key == "buddy":
            if not value:
                return f"Buddy is currently {'on' if cfg.buddy_enabled else 'off'}."
            enabled = _parse_on_off(value)
            if enabled is None:
                return "Usage: /config buddy [on|off]"
            save_global_config({"buddy_enabled": enabled})
            return f"Saved buddy setting: {'on' if enabled else 'off'} (used on next session start)"

        return f"Unknown config section '{key}'. Try /config with no arguments."


def _runtime_provider_name(agent: Agent) -> str:
    provider = getattr(agent, "provider", None)
    provider_config = getattr(provider, "_config", None)
    provider_type = getattr(provider_config, "type", "")
    if provider_type:
        return str(provider_type)
    cfg = getattr(agent, "_cli_cfg", None)
    return getattr(cfg, "provider", "anthropic")


def _apply_runtime_api_key(agent: Agent, api_key: str) -> None:
    provider = getattr(agent, "provider", None)
    if provider is None:
        return
    provider_config = getattr(provider, "_config", None)
    if provider_config is not None:
        provider_config.api_key = api_key
    if hasattr(provider, "_client"):
        provider._client = None


class LoginCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "login"

    @property
    def description(self) -> str:
        return "Persist a provider login state using an API key"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..auth import list_auth_providers, mask_secret, save_provider_auth
        from ..config import load_config, save_global_config

        parts = shlex.split(args)
        current_provider = _runtime_provider_name(agent)
        cfg = load_config()
        supported = ["anthropic", "openai", "compatible", "ollama", "vllm", "deepseek"]

        if not parts or parts[0].lower() == "status":
            entries = list_auth_providers()
            lines = [
                "Login status:",
                f"  current provider: {current_provider}",
                f"  current session api_key: {'set' if cfg.api_key else '(unset)'}",
            ]
            if entries:
                lines.append("  stored providers:")
                for entry in entries:
                    label = f" ({entry.account_label})" if entry.account_label else ""
                    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.updated_at))
                    lines.append(
                        f"    - {entry.provider}{label}: {mask_secret(entry.api_key)} updated {stamp}"
                    )
            else:
                lines.append("  stored providers: (none)")
            lines.extend([
                "",
                "Usage:",
                "  /login <api_key>",
                "  /login <provider> <api_key> [account label]",
                "  /login status",
            ])
            return "\n".join(lines)

        if len(parts) == 1:
            provider_name = current_provider
            api_key = parts[0]
            account_label = ""
        else:
            provider_name = parts[0].lower()
            api_key = parts[1]
            account_label = " ".join(parts[2:])

        if provider_name not in supported:
            return f"Unsupported provider '{provider_name}'. Available: {', '.join(supported)}"
        if not api_key.strip():
            return "Usage: /login <api_key> or /login <provider> <api_key> [account label]"

        save_provider_auth(provider_name, api_key, account_label=account_label)
        save_global_config({"provider": provider_name})

        updated_session = False
        if provider_name == current_provider:
            _apply_runtime_api_key(agent, api_key)
            updated_session = True

        suffix = " Current session updated." if updated_session else ""
        label_suffix = f" ({account_label})" if account_label else ""
        return f"Stored login for {provider_name}{label_suffix}.{suffix}"


class LogoutCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "logout"

    @property
    def description(self) -> str:
        return "Clear persisted login state"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..auth import clear_all_auth, clear_provider_auth
        from ..config import save_global_config

        parts = shlex.split(args)
        current_provider = _runtime_provider_name(agent)

        if not parts:
            target = current_provider
        else:
            target = parts[0].lower()

        if target == "all":
            clear_all_auth()
            save_global_config({"api_key": ""})
            _apply_runtime_api_key(agent, "")
            return "Cleared all stored login state."

        removed = clear_provider_auth(target)
        if target == current_provider:
            save_global_config({"api_key": ""})
            _apply_runtime_api_key(agent, "")

        if not removed:
            return f"No stored login state found for {target}."
        return f"Cleared stored login for {target}."


class PluginCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "plugin"

    @property
    def aliases(self) -> list[str]:
        return ["plugins", "marketplace"]

    @property
    def description(self) -> str:
        return "Manage loaded mini-agent plugins"

    async def execute(self, args: str, agent: Agent) -> str:
        registry = getattr(agent, "_plugin_registry", None)

        parts = args.strip().split()
        action = parts[0].lower() if parts else "status"

        if registry is None:
            if action in {"status", "list"}:
                return "No plugins loaded."
            return "Plugin runtime is not initialized."

        if action in {"status", "list"}:
            return registry.status_summary()

        if action == "reload":
            return "Plugin reload is not supported."

        if len(parts) < 2:
            return "Usage: /plugin [status|list|reload|enable <name>|disable <name>]"

        target = parts[1]
        if action == "enable":
            if not registry.enable(target):
                return f"Plugin '{target}' not found."
        elif action == "disable":
            if not registry.disable(target):
                return f"Plugin '{target}' not found."
        else:
            return "Usage: /plugin [status|list|reload|enable <name>|disable <name>]"

        return registry.status_summary()


class McpCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "mcp"

    @property
    def description(self) -> str:
        return "Show MCP server configuration, connection status, and tools"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..mcp.manager import auto_discover_servers, get_mcp_manager

        action = args.strip().lower()
        manager = get_mcp_manager()
        configured = auto_discover_servers()

        if action == "tools":
            if manager is None:
                return "MCP manager is not attached to this runtime yet."
            tools = manager.get_tools()
            if not tools:
                return "No MCP tools are currently connected."
            lines = [f"MCP tools ({len(tools)}):"]
            for tool in tools:
                lines.append(f"  - {tool.name}")
            return "\n".join(lines)

        if action not in {"", "status", "list"}:
            return "Usage: /mcp [status|list|tools]"

        lines = [
            "MCP:",
            f"  runtime_manager: {'attached' if manager is not None else 'not attached'}",
            f"  configured_servers: {len(configured)}",
        ]
        if manager is not None:
            lines.append(f"  connected_servers: {manager.connected_count}")

        if configured:
            lines.append("  configured:")
            for name, cfg in sorted(configured.items()):
                url = str(getattr(cfg, "url", "") or "").strip()
                if url:
                    transport = "http"
                    target = url
                else:
                    transport = "stdio"
                    command = str(getattr(cfg, "command", "") or "").strip()
                    raw_args = list(getattr(cfg, "args", []) or [])
                    preview = " ".join(str(value) for value in raw_args[:3])
                    if len(raw_args) > 3:
                        preview = f"{preview} ..."
                    target = f"{command} {preview}".strip() or "(unset)"
                lines.append(f"    - {name} [{transport}]: {target}")
        else:
            lines.append("  configured: (none)")

        if manager is not None:
            lines.extend([
                "",
                manager.status_summary(),
            ])

        return "\n".join(lines)


class HooksCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "hooks"

    @property
    def description(self) -> str:
        return "Show loaded runtime hooks and configured user hook scripts"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..hooks.user_scripts import discover_user_hooks, load_hook_config

        action = args.strip().lower()
        if action not in {"", "status", "list"}:
            return "Usage: /hooks [status|list]"

        runner = getattr(agent, "_hook_runner", None)
        runtime_counts = [
            ("pre_query", len(getattr(runner, "pre_query", []))),
            ("post_query", len(getattr(runner, "post_query", []))),
            ("stream_event", len(getattr(runner, "stream_event", []))),
            ("pre_tool_use", len(getattr(runner, "pre_tool_use", []))),
            ("post_tool_use", len(getattr(runner, "post_tool_use", []))),
            ("session_start", len(getattr(runner, "session_start", []))),
            ("session_end", len(getattr(runner, "session_end", []))),
            ("stop", len(getattr(runner, "stop", []))),
            ("notification", len(getattr(runner, "notification", []))),
            ("post_sampling", len(getattr(runner, "post_sampling", []))),
        ]
        configured = load_hook_config(os.getcwd())
        scripts = discover_user_hooks()

        lines = [
            "Hooks:",
            f"  runtime_hooks: {sum(count for _, count in runtime_counts)}",
        ]
        for label, count in runtime_counts:
            if count > 0:
                lines.append(f"    - {label}: {count}")

        if configured:
            lines.append("  configured_events:")
            for event_name, matchers in sorted(configured.items()):
                hook_count = sum(len(getattr(matcher, "hooks", [])) for matcher in matchers)
                lines.append(
                    f"    - {event_name}: {len(matchers)} matcher(s), {hook_count} hook(s)"
                )
        else:
            lines.append("  configured_events: (none)")

        if scripts:
            lines.append("  python_hook_scripts:")
            for script in scripts[:10]:
                lines.append(f"    - {script.path.name} -> {script.event.value}")
            if len(scripts) > 10:
                lines.append(f"    - ... {len(scripts) - 10} more")
        else:
            lines.append("  python_hook_scripts: (none)")

        return "\n".join(lines)


class TasksCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "tasks"

    @property
    def description(self) -> str:
        return "Show runtime background tasks and the current task board"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..tools.task_tools import TaskBoard

        action = args.strip().lower()
        if action not in {"", "status", "list", "runtime", "board", "all"}:
            return "Usage: /tasks [status|list|runtime|board|all]"

        include_completed = action == "all"
        runtime_tasks = agent.task_manager.list_tasks(include_completed=include_completed)
        task_list_id = _resolve_task_list_id_for_agent(agent)
        board = TaskBoard()
        board.set_scope(task_list_id)
        board_tasks = [
            task for task in board.list()
            if not task.metadata.get("_internal")
        ]

        lines = ["Tasks:"]

        if action in {"", "status", "list", "runtime", "all"}:
            lines.append(f"  runtime_tasks: {len(runtime_tasks)}")
            if runtime_tasks:
                for task in runtime_tasks[:10]:
                    status = task.status.value if hasattr(task.status, "value") else str(task.status)
                    lines.append(f"    - [{status}] {task.id}: {task.description}")
                if len(runtime_tasks) > 10:
                    lines.append(f"    - ... {len(runtime_tasks) - 10} more")
            else:
                lines.append("    - none")

        if action in {"", "status", "list", "board", "all"}:
            lines.append(f"  board_scope: {task_list_id}")
            lines.append(f"  board_tasks: {len(board_tasks)}")
            if board_tasks:
                for task in board_tasks[:10]:
                    lines.append(f"    - [{task.status}] {task.id}: {task.subject}")
                if len(board_tasks) > 10:
                    lines.append(f"    - ... {len(board_tasks) - 10} more")
            else:
                lines.append("    - none")

        return "\n".join(lines)


class SessionCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "session"

    @property
    def description(self) -> str:
        return "Inspect the current session and recently persisted sessions"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..session.store import _build_session_preview

        store = getattr(agent, "_session_store", None)
        if store is None:
            return "Session persistence is disabled in this runtime."

        parts = args.strip().split()
        action = parts[0].lower() if parts else "status"

        if action in {"", "status", "current"}:
            latest = store.get_latest_session()
            return "\n".join([
                "Session:",
                f"  current: {agent.conversation_id}",
                f"  current_messages: {len(agent.messages)}",
                f"  persistence_dir: {_display_path_from_cwd(store.session_dir)}",
                f"  latest_saved: {latest or '(none)'}",
                "",
                "Usage:",
                "  /session",
                "  /session list [limit]",
                "  /session latest",
                "  /session show <session-id>",
            ])

        if action in {"list", "recent"}:
            limit = 10
            if len(parts) > 1 and parts[1].isdigit():
                limit = max(1, int(parts[1]))
            sessions = store.list_sessions(limit=limit)
            if not sessions:
                return "No persisted sessions were found."
            lines = [f"Sessions ({len(sessions)} shown):"]
            for info in sessions:
                title = f" — {info.title}" if info.title else ""
                preview = f" | {info.preview}" if info.preview else ""
                lines.append(
                    f"  - {info.session_id}{title} [{info.message_count} msg]{preview}"
                )
            return "\n".join(lines)

        if action == "latest":
            latest = store.get_latest_session()
            if not latest:
                return "No persisted sessions were found."
            parts = ["show", latest]

        if action == "show" and len(parts) >= 2:
            session_id = parts[1]
            messages = store.load_session(session_id)
            metadata = store.load_metadata(session_id)
            if not messages and metadata is None:
                return f"No persisted session found for: {session_id}"
            preview = _build_session_preview(messages) if messages else ""
            lines = [
                "Session details:",
                f"  session_id: {session_id}",
                f"  title: {getattr(metadata, 'title', '') or '(unset)'}",
                f"  cwd: {getattr(metadata, 'cwd', '') or '(unset)'}",
                f"  message_count: {getattr(metadata, 'message_count', len(messages))}",
                f"  tags: {', '.join(getattr(metadata, 'tags', []) or []) or '(none)'}",
                f"  pending_run_id: {getattr(metadata, 'pending_run_id', '') or '(none)'}",
            ]
            if preview:
                lines.append(f"  preview: {preview}")
            return "\n".join(lines)

        return "Usage: /session [status|list [limit]|latest|show <session-id>]"


class RewindCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "rewind"

    @property
    def aliases(self) -> list[str]:
        return ["checkpoint"]

    @property
    def description(self) -> str:
        return "Remove recent conversation messages from the current session"

    async def execute(self, args: str, agent: Agent) -> str:
        if not agent._messages:
            return "No messages to rewind."

        text = args.strip()
        if text.isdigit():
            count = max(1, int(text))
        else:
            count = 0
            for msg in reversed(agent._messages):
                count += 1
                if msg.role == "user":
                    break
            count = max(count, 1)

        removed = agent._messages[-count:]
        del agent._messages[-count:]
        agent._pending_client_run_id = None
        agent._pending_client_calls = []
        agent._persist_session_snapshot()

        return (
            f"Rewound {len(removed)} message(s). "
            f"{len(agent._messages)} message(s) remain in the current session."
        )


class AgentsCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "agents"

    @property
    def description(self) -> str:
        return "Show available agent types and current agent runtime state"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..delegation.builtin_agents import BuiltInAgentRegistry

        registry = getattr(agent, "_builtin_agent_registry", None)
        if registry is None:
            registry = BuiltInAgentRegistry()

        parts = args.strip().split(maxsplit=1)
        if parts:
            selected = registry.get(parts[0])
            if selected is None:
                return f"Unknown agent type '{parts[0]}'."
            tools = ", ".join(selected.tools)
            return (
                f"Agent: {selected.agent_type}\n"
                f"  model: {selected.model}\n"
                f"  read_only: {selected.read_only}\n"
                f"  background: {selected.background}\n"
                f"  tools: {tools}\n"
                f"  when_to_use: {selected.when_to_use}"
            )

        lines = ["Available agents:"]
        for definition in registry.list_definitions():
            flags: list[str] = []
            if definition.read_only:
                flags.append("read-only")
            if definition.background:
                flags.append("background")
            flag_text = f" [{', '.join(flags)}]" if flags else ""
            lines.append(
                f"  - {definition.agent_type}{flag_text}: {definition.when_to_use}"
            )
        coordinator = getattr(agent, "_coordinator_mode", None)
        lines.append("")
        lines.append(
            f"Coordinator mode: {'on' if getattr(coordinator, 'is_active', False) else 'off'}"
        )
        return "\n".join(lines)


class SkillsCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "skills"

    @property
    def description(self) -> str:
        return "List available bundled, local, plugin, and MCP skills"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..commands.types import CommandSource, CommandType
        from ..skills import SkillLoader, discover_skill_dirs_for_path
        from ..skills.bundled import get_bundled_skills

        parts = args.strip().split(maxsplit=1)
        query = parts[0].lower() if parts else ""

        skill_dirs = discover_skill_dirs_for_path(os.getcwd())
        loader = SkillLoader(skill_dirs=skill_dirs)
        local_skills = loader.discover() if skill_dirs else []
        bundled = get_bundled_skills()
        all_commands = agent._command_registry.get_all_commands()

        plugin_skills = [
            command
            for command in all_commands
            if command.type == CommandType.PROMPT and command.source == CommandSource.PLUGIN
        ]
        mcp_skills = [
            command
            for command in all_commands
            if command.type == CommandType.PROMPT and command.source == CommandSource.MCP
        ]

        if query:
            matches: list[str] = []
            for skill in local_skills:
                if query in skill.name.lower() or query in skill.description.lower():
                    matches.append(f"local: {skill.name} — {skill.description}")
            for name, skill in bundled.items():
                if query in name.lower() or query in skill.description.lower():
                    matches.append(f"bundled: {name} — {skill.description}")
            for command in plugin_skills:
                if query in command.name.lower() or query in command.description.lower():
                    matches.append(f"plugin: {command.name} — {command.description}")
            for command in mcp_skills:
                if query in command.name.lower() or query in command.description.lower():
                    matches.append(f"mcp: {command.name} — {command.description}")
            if not matches:
                return f"No skills matched '{query}'."
            return "Matching skills:\n" + "\n".join(f"  - {item}" for item in matches[:20])

        lines = [
            "Skills:",
            f"  bundled: {len(bundled)}",
            f"  local: {len(local_skills)}",
            f"  plugin: {len(plugin_skills)}",
            f"  mcp: {len(mcp_skills)}",
            "",
        ]
        if bundled:
            lines.append("Bundled skills:")
            for name, skill in sorted(bundled.items()):
                lines.append(f"  - {name}: {skill.description}")
            lines.append("")
        if local_skills:
            lines.append("Local skills:")
            for skill in sorted(local_skills, key=lambda item: item.name):
                lines.append(f"  - {skill.name}: {skill.description}")
            lines.append("")
        if plugin_skills:
            lines.append("Plugin skills:")
            for command in plugin_skills:
                lines.append(f"  - {command.name}: {command.description}")
            lines.append("")
        if mcp_skills:
            lines.append("MCP skills:")
            for command in mcp_skills:
                lines.append(f"  - {command.name}: {command.description}")
            lines.append("")
        lines.append("Usage: /skills [query]")
        return "\n".join(lines).rstrip()


class StatusCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "status"

    @property
    def description(self) -> str:
        return "Show a compact runtime status overview"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..config import load_config
        from ..tools.list_peers import count_live_sessions

        cfg = load_config()
        plugin_registry = getattr(agent, "_plugin_registry", None)
        plugin_count = len(plugin_registry.plugins) if plugin_registry is not None else 0
        pending = getattr(agent, "_pending_client_run_id", None)
        summary = agent.get_current_summary().strip()

        lines = [
            "Status:",
            f"  provider: {cfg.provider}",
            f"  model: {agent.provider.model_name or cfg.model or '(default)'}",
            f"  cwd: {os.getcwd()}",
            f"  session: {agent.conversation_id}",
            f"  messages: {len(agent.messages)}",
            f"  tools: {len(agent.tools)}",
            f"  plugins: {plugin_count}",
            f"  live_sessions: {count_live_sessions()}",
            f"  pending_client_run: {pending or '(none)'}",
            f"  coordinator: {'on' if getattr(getattr(agent, '_coordinator_mode', None), 'is_active', False) else 'off'}",
            f"  kairos: {'on' if cfg.kairos_enabled else 'off'}",
        ]
        if summary:
            lines.append(f"  summary: {summary}")
        return "\n".join(lines)


class StatsCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "stats"

    @property
    def description(self) -> str:
        return "Show session and cumulative usage statistics"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..services.stats import load_cumulative_stats

        verbose = args.strip().lower() in {"verbose", "--verbose", "-v"}
        session_summary = agent.stats_tracker.summary(verbose=verbose)
        cumulative = load_cumulative_stats()
        lines = [
            session_summary,
            "",
            "Cumulative:",
            f"  sessions: {cumulative.get('sessions', 0)}",
            f"  requests: {cumulative.get('total_requests', 0)}",
            f"  tokens: {cumulative.get('total_tokens', 0):,}",
            f"  cost: ${cumulative.get('total_cost_usd', 0.0):.4f}",
        ]
        return "\n".join(lines)


class UsageCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "usage"

    @property
    def description(self) -> str:
        return "Show token, cost, and budget usage for the current session"

    async def execute(self, args: str, agent: Agent) -> str:
        verbose = args.strip().lower() in {"verbose", "--verbose", "-v"}
        usage_summary = agent.usage_tracker.summary()
        lines = [
            "Usage:",
            f"  calls: {usage_summary.get('calls', 0)}",
            f"  input_tokens: {usage_summary.get('total_input_tokens', 0):,}",
            f"  output_tokens: {usage_summary.get('total_output_tokens', 0):,}",
            f"  total_tokens: {usage_summary.get('total_tokens', 0):,}",
            f"  total_cost_usd: ${usage_summary.get('total_cost_usd', 0.0):.4f}",
        ]

        budget = agent.budget_status
        if budget is not None:
            status_text = budget.status_text() or "(no explicit limits configured)"
            budget_check = budget.check()
            lines.extend([
                "",
                "Budget:",
                f"  status: {status_text}",
                f"  state: {budget_check.action or 'ok'}",
            ])

        lines.extend([
            "",
            agent.stats_tracker.summary(verbose=verbose),
        ])
        return "\n".join(lines)


class PermissionsCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "permissions"

    @property
    def aliases(self) -> list[str]:
        return ["allowed-tools"]

    @property
    def description(self) -> str:
        return "Show or change the default permission mode"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..config import load_config, save_global_config
        from ..permissions import PermissionDecision, PermissionMode

        value = args.strip()
        modes = [mode.value for mode in PermissionMode]
        cfg = load_config()
        parts = value.split()
        raw_rules = list(cfg.permission_rules)

        def render_rules() -> str:
            if not raw_rules:
                return "  rules: (none)"
            lines = ["  rules:"]
            for index, rule in enumerate(raw_rules, start=1):
                reason = rule.get("reason", "")
                suffix = f" ({reason})" if reason else ""
                lines.append(
                    f"    {index}. {rule.get('decision', '?')} {rule.get('tool_pattern', '?')}{suffix}"
                )
            return "\n".join(lines)

        if not parts:
            return (
                "Permissions:\n"
                f"  current mode: {cfg.permission_mode}\n"
                f"  available: {', '.join(modes)}\n"
                f"{render_rules()}\n"
                "\n"
                "Usage:\n"
                "  /permissions mode <mode>\n"
                "  /permissions allow <tool-pattern> [reason]\n"
                "  /permissions deny <tool-pattern> [reason]\n"
                "  /permissions remove <tool-pattern>\n"
                "  /permissions clear\n"
                "  /permissions check <tool-name>"
            )

        command = parts[0].lower()

        if command in modes:
            save_global_config({"permission_mode": command})
            _refresh_permission_runtime(agent)
            return f"Saved permission mode: {command}"

        if command == "mode":
            if len(parts) < 2 or parts[1] not in modes:
                return f"Usage: /permissions mode <{'|'.join(modes)}>"
            selected_mode = parts[1]
            save_global_config({"permission_mode": selected_mode})
            _refresh_permission_runtime(agent)
            return f"Saved permission mode: {selected_mode}"

        if command in {"allow", "deny"}:
            if len(parts) < 2:
                return f"Usage: /permissions {command} <tool-pattern> [reason]"
            pattern = parts[1]
            reason = " ".join(parts[2:]).strip()
            decision = PermissionDecision.ALLOW if command == "allow" else PermissionDecision.DENY
            raw_rules = [rule for rule in raw_rules if rule.get("tool_pattern") != pattern]
            raw_rules.append(
                {
                    "tool_pattern": pattern,
                    "decision": decision.value,
                    "reason": reason,
                }
            )
            save_global_config({"permission_rules": raw_rules})
            _refresh_permission_runtime(agent)
            return f"Saved permission rule: {decision.value} {pattern}"

        if command == "remove":
            if len(parts) < 2:
                return "Usage: /permissions remove <tool-pattern>"
            pattern = parts[1]
            updated_rules = [rule for rule in raw_rules if rule.get("tool_pattern") != pattern]
            if len(updated_rules) == len(raw_rules):
                return f"No permission rule found for pattern: {pattern}"
            save_global_config({"permission_rules": updated_rules})
            _refresh_permission_runtime(agent)
            return f"Removed permission rule: {pattern}"

        if command == "clear":
            save_global_config({"permission_rules": []})
            _refresh_permission_runtime(agent)
            return "Cleared all permission rules."

        if command == "check":
            if len(parts) < 2:
                return "Usage: /permissions check <tool-name>"
            tool_name = parts[1]
            checker = getattr(agent, "_permission_checker", None)
            if checker is None:
                _refresh_permission_runtime(agent)
                checker = getattr(agent, "_permission_checker", None)
            assert checker is not None
            tool = next((tool for tool in agent.tools if tool.name == tool_name), None)
            decision = checker.check(tool_name, is_read_only=bool(tool and tool.is_read_only))
            return (
                "Permission check:\n"
                f"  tool: {tool_name}\n"
                f"  mode: {cfg.permission_mode}\n"
                f"  decision: {decision.value}\n"
                f"{render_rules()}"
            )

        return "Unknown permissions command. Run /permissions for usage."


class DoctorCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "doctor"

    @property
    def description(self) -> str:
        return "Diagnose common CLI configuration and runtime issues"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..config import _global_config_path, load_config, validate_config
        from ..delegation.coordinator import is_coordinator_mode
        from ..kairos import is_kairos_active
        from ..tools.list_peers import count_live_sessions
        from ..tools.plan_mode import is_plan_mode_active
        from ..session.store import SessionStore

        cfg = load_config()
        errors = validate_config(cfg)
        session_name = getattr(agent, "_session_name", "").strip() or "(unset)"
        session_dir = Path(cfg.session_dir)
        config_path = _global_config_path()
        store = SessionStore(session_dir)
        saved_sessions = store.list_sessions(limit=5)
        permission_checker = getattr(agent, "_permission_checker", None)
        provider_config = getattr(agent.provider, "_config", None)

        def status_line_item(status: str, label: str, detail: str) -> str:
            return f"  [{status}] {label}: {detail}"

        checks: list[str] = []
        warnings: list[str] = []

        checks.append(status_line_item("OK", "cwd", os.getcwd()))
        checks.append(status_line_item("OK", "python", sys.executable))
        checks.append(status_line_item("OK", "provider", cfg.provider))
        checks.append(status_line_item("OK", "model", agent.provider.model_name or "(unset)"))

        if cfg.provider != "mock" and not cfg.api_key:
            warnings.append("API key is not configured in config/env for the selected provider.")
            checks.append(status_line_item("WARN", "api_key", "missing"))
        else:
            checks.append(status_line_item("OK", "api_key", "set" if cfg.api_key else "not required"))

        if cfg.base_url:
            checks.append(status_line_item("OK", "base_url", cfg.base_url))
        else:
            checks.append(status_line_item("OK", "base_url", "(default)"))

        if config_path.exists():
            checks.append(status_line_item("OK", "global_config", str(config_path)))
        else:
            warnings.append("Global config file does not exist yet; current settings may only be coming from defaults or env.")
            checks.append(status_line_item("WARN", "global_config", f"missing at {config_path}"))

        if session_dir.exists():
            checks.append(status_line_item("OK", "session_dir", str(session_dir)))
        else:
            warnings.append("Session directory does not exist yet; persistence may not have run yet.")
            checks.append(status_line_item("WARN", "session_dir", str(session_dir)))

        checks.append(status_line_item("OK", "session_persistence", "on" if cfg.session_persistence else "off"))
        checks.append(status_line_item("OK", "saved_sessions", str(len(saved_sessions))))
        checks.append(status_line_item("OK", "live_sessions", str(count_live_sessions())))
        checks.append(status_line_item("OK", "session_id", agent.conversation_id))
        checks.append(status_line_item("OK", "session_name", session_name))
        checks.append(status_line_item("OK", "tools_enabled", "on" if cfg.tools_enabled else "off"))
        checks.append(status_line_item("OK", "loaded_tools", str(len(agent.tools))))
        registry = getattr(agent, "_builtin_agent_registry", None)
        if registry is not None:
            checks.append(status_line_item("OK", "builtin_agent_types", ", ".join(registry.list_types()) or "(none)"))
        checks.append(status_line_item("OK", "builtin_explore_plan_agents_enabled", "on" if cfg.builtin_explore_plan_agents_enabled else "off"))
        checks.append(status_line_item("OK", "builtin_verification_agent_enabled", "on" if cfg.builtin_verification_agent_enabled else "off"))
        checks.append(status_line_item("OK", "commands", str(len(agent._command_registry.get_all_commands()))))
        checks.append(status_line_item("OK", "theme", cfg.theme))
        checks.append(status_line_item("OK", "statusline_enabled", "on" if cfg.statusline_enabled else "off"))
        checks.append(status_line_item("OK", "coordinator_enabled", "on" if cfg.coordinator_enabled else "off"))
        checks.append(status_line_item("OK", "coordinator_mode", "on" if is_coordinator_mode() else "off"))
        checks.append(status_line_item("OK", "permission_mode", cfg.permission_mode))
        checks.append(status_line_item("OK", "permission_rules", str(len(cfg.permission_rules))))

        if permission_checker is None:
            warnings.append("Runtime permission checker is not attached; tool permission rules will not be enforced.")
            checks.append(status_line_item("WARN", "permission_runtime", "missing"))
        else:
            checks.append(status_line_item("OK", "permission_runtime", "attached"))

        checks.append(status_line_item("OK", "plan_mode", "on" if is_plan_mode_active() else "off"))
        checks.append(status_line_item("OK", "kairos", "on" if is_kairos_active() else "off"))

        if provider_config is not None and not getattr(provider_config, "model", ""):
            warnings.append("Provider model is blank in persisted config; runtime may be relying on provider defaults.")
            checks.append(status_line_item("WARN", "provider_config_model", "(blank)"))
        else:
            checks.append(status_line_item("OK", "provider_config_model", getattr(provider_config, "model", "(unknown)")))

        lines = ["Doctor:", "", "Checks:"]
        lines.extend(checks)

        if errors:
            lines.extend([
                "",
                "Config validation:",
                "  [FAIL] One or more persisted config values are invalid.",
            ])
            lines.extend(f"    - {error}" for error in errors)
        else:
            lines.extend(["", "Config validation:", "  [OK] Config values passed validation."])

        if warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"  - {warning}" for warning in warnings)
        else:
            lines.extend(["", "Warnings:", "  - none"])

        lines.extend([
            "",
            "Next steps:",
            "  - Run /config to inspect or fix persisted settings.",
            "  - Run /permissions to inspect active permission rules.",
            "  - Run /statusline on if you want the interactive bottom status bar.",
        ])
        return "\n".join(lines)


class TerminalSetupCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "terminal-setup"

    @property
    def description(self) -> str:
        return "Show terminal multiline and integration setup guidance"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..config import load_config

        sub = args.strip().lower()
        cfg = load_config()
        terminal_id, terminal_name = _detect_terminal_environment()
        native_shift_enter = _terminal_supports_native_shift_enter(terminal_id)
        shell_name = Path(os.environ.get("SHELL", os.environ.get("COMSPEC", ""))).name or "unknown"
        manual_hint = {
            "apple_terminal": "Apple Terminal usually needs Option+Enter style terminal mapping; keep mini-agent on alt-enter if Shift+Enter does not arrive.",
            "vscode": "VS Code/Cursor/Windsurf/Zed usually need a terminal keybinding that sends Escape+Enter for Shift+Enter.",
            "cursor": "VS Code/Cursor/Windsurf/Zed usually need a terminal keybinding that sends Escape+Enter for Shift+Enter.",
            "windsurf": "VS Code/Cursor/Windsurf/Zed usually need a terminal keybinding that sends Escape+Enter for Shift+Enter.",
            "zed": "VS Code/Cursor/Windsurf/Zed usually need a terminal keybinding that sends Escape+Enter for Shift+Enter.",
            "alacritty": "Alacritty usually needs a Shift+Enter binding that emits Escape+Enter.",
            "windows_terminal": "Windows Terminal may pass Alt+Enter differently; if Shift+Enter does not create a newline, keep mini-agent on alt-enter and use the terminal's keybinding settings.",
            "unknown": "If your terminal does not forward Shift+Enter distinctly, configure it to send Escape+Enter, or switch mini-agent to alt-enter.",
        }.get(terminal_id, "No extra setup should be needed in this terminal.")

        def status_text(extra_lines: list[str] | None = None) -> str:
            lines = [
                "Terminal setup:",
                f"  platform: {sys.platform}",
                f"  shell: {shell_name}",
                f"  terminal: {terminal_name}",
                f"  native_shift_enter: {'yes' if native_shift_enter else 'no'}",
                f"  configured_multiline_key: {cfg.multiline_key}",
                "",
                "Behavior:",
                "  - Enter sends the prompt.",
                f"  - {cfg.multiline_key.title()} is the configured newline shortcut shown in the footer.",
                "  - The prompt UI interprets terminal Escape+Enter sequences as a newline.",
            ]
            if extra_lines:
                lines.extend(["", *extra_lines])
            lines.extend(["", "Setup hint:", f"  - {manual_hint}", "", _render_terminal_setup_help()])
            return "\n".join(lines)

        if sub in {"", "status"}:
            return status_text()

        if sub == "auto":
            selected = "shift+enter" if native_shift_enter else "alt+enter"
            path = _save_multiline_preference(selected)
            setattr(agent, "_multiline_key", selected)
            return status_text(
                [
                    f"Saved multiline preference: {selected}",
                    f"Config updated: {path}",
                ]
            )

        if sub in {"shift-enter", "shift+enter"}:
            path = _save_multiline_preference("shift+enter")
            setattr(agent, "_multiline_key", "shift+enter")
            return status_text(
                [
                    "Saved multiline preference: shift+enter",
                    f"Config updated: {path}",
                ]
            )

        if sub in {"alt-enter", "alt+enter"}:
            path = _save_multiline_preference("alt+enter")
            setattr(agent, "_multiline_key", "alt+enter")
            return status_text(
                [
                    "Saved multiline preference: alt+enter",
                    f"Config updated: {path}",
                ]
            )

        if sub == "reset":
            path = _save_multiline_preference("shift+enter")
            setattr(agent, "_multiline_key", "shift+enter")
            return status_text(
                [
                    "Reset multiline preference to: shift+enter",
                    f"Config updated: {path}",
                ]
            )

        return f"Unknown terminal-setup action '{sub}'.\n\n{_render_terminal_setup_help()}"


class StatuslineCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "statusline"

    @property
    def description(self) -> str:
        return "Show, enable, or disable the interactive status line"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..config import load_config, save_global_config

        value = args.strip().lower()
        status_line = getattr(agent, "_status_line", None)
        current = bool(getattr(agent, "_statusline_enabled", load_config().statusline_enabled))

        if value in {"", "status"}:
            return f"Status line is currently {'on' if current else 'off'}."

        if value not in {"on", "off", "toggle"}:
            return "Usage: /statusline [on|off|toggle|status]"

        enabled = not current if value == "toggle" else value == "on"
        setattr(agent, "_statusline_enabled", enabled)
        save_global_config({"statusline_enabled": enabled})

        if status_line is not None:
            if enabled:
                status_line.show()
            else:
                status_line.hide()

        return f"Status line {'enabled' if enabled else 'disabled'}."


class FeedbackCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "feedback"

    @property
    def aliases(self) -> list[str]:
        return ["bug"]

    @property
    def description(self) -> str:
        return "Save CLI feedback locally"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..config import _home_dir

        feedback = args.strip()
        feedback_path = _home_dir() / "feedback.log"
        if not feedback:
            return f"Usage: /feedback <message>\nFeedback file: {feedback_path}"

        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        with feedback_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"[session={agent.conversation_id}] "
                f"[model={agent.provider.model_name}] "
                f"{feedback}\n"
            )
        return f"Saved feedback to: {feedback_path}"


class CostCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "cost"

    @property
    def description(self) -> str:
        return "Show session token usage and cost"

    async def execute(self, args: str, agent: Agent) -> str:
        t = agent.usage_tracker
        return (
            f"Session usage:\n"
            f"  Calls: {t.call_count}\n"
            f"  Input tokens: {t.total_input_tokens:,}\n"
            f"  Output tokens: {t.total_output_tokens:,}\n"
            f"  Cache read: {t.total_cache_read_tokens:,}\n"
            f"  Cache creation: {t.total_cache_creation_tokens:,}\n"
            f"  Total cost: ${t.total_cost():.4f}"
        )


class ModelCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "model"

    @property
    def description(self) -> str:
        return "Show current model"

    async def execute(self, args: str, agent: Agent) -> str:
        return f"Current model: {agent.provider.model_name}"


class VoiceCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "voice"

    @property
    def enabled(self) -> bool:
        from ..auth import get_provider_api_key

        return bool(get_provider_api_key("anthropic"))

    @property
    def hidden(self) -> bool:
        return not self.enabled

    @property
    def description(self) -> str:
        return "Show voice status or record a short voice input"

    async def execute(self, args: str, agent: Agent) -> str:
        from ..services.voice import (
            AudioBackend,
            VoiceError,
            check_recording_availability,
            check_voice_dependencies,
            has_stt_callback,
            is_recording,
            probe_audio_devices,
            record_and_transcribe,
            register_stt,
            request_microphone_permission,
            start_recording,
            stop_recording,
            transcribe_wav,
            cancel_recording,
        )

        sub = args.strip().lower()
        if sub == "devices":
            devices = probe_audio_devices()
            if not devices:
                return "Voice:\n  devices: none detected"
            lines = ["Voice devices:"]
            lines.extend(
                f"  - {device.name} (id={device.id}, rate={device.sample_rate}, channels={device.channels})"
                for device in devices
            )
            return "\n".join(lines)

        if sub == "permission":
            granted = request_microphone_permission()
            return f"Voice microphone permission: {'granted' if granted else 'not granted'}"

        if sub == "start":
            backend = check_recording_availability()
            if backend == AudioBackend.NONE:
                return "Voice:\n  backend: none\n  message: recording is unavailable in this environment."
            try:
                started = start_recording(backend)
            except VoiceError as exc:
                return f"Voice recording failed to start: {exc}"
            return f"Voice recording started with backend: {started.value}"

        if sub == "stop":
            if not is_recording():
                return "Voice recording is not active."
            try:
                wav_data = stop_recording()
            except VoiceError as exc:
                return f"Voice recording failed to stop: {exc}"
            if not wav_data:
                return "Voice transcript: (empty)"
            if not has_stt_callback():
                register_stt(lambda _wav: "[voice transcript unavailable: no STT backend configured]")
            try:
                text = await transcribe_wav(wav_data)
            except VoiceError as exc:
                return f"Voice transcription failed: {exc}"
            return f"Voice transcript: {text or '(empty)'}"

        if sub == "cancel":
            cancel_recording()
            return "Voice recording cancelled."

        if sub == "record":
            backend = check_recording_availability()
            if backend == AudioBackend.NONE:
                return "Voice:\n  backend: none\n  message: recording is unavailable in this environment."

            register_stt(lambda _wav: "[voice transcript unavailable: no STT backend configured]")
            try:
                text = await record_and_transcribe(backend=backend, timeout=3.0)
            except VoiceError as exc:
                return f"Voice recording failed: {exc}"
            return f"Voice transcript: {text or '(empty)'}"

        if sub and sub != "status":
            return "Usage: /voice [status|record|start|stop|cancel|devices|permission]"
        backend = check_recording_availability()
        deps = check_voice_dependencies()
        return (
            "Voice:\n"
            f"  backend: {backend.value}\n"
            f"  recording: {'on' if is_recording() else 'off'}\n"
            f"  stt_registered: {'yes' if has_stt_callback() else 'no'}\n"
            f"  dependencies: {deps}"
        )


class ExitCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "exit"

    @property
    def aliases(self) -> list[str]:
        return ["quit"]

    @property
    def description(self) -> str:
        return "Exit the session"

    async def execute(self, args: str, agent: Agent) -> str:
        return "__EXIT__"


class VersionCommand(SlashCommand):
    @property
    def name(self) -> str:
        return "version"

    @property
    def description(self) -> str:
        return "Show ccmini runtime version information"

    async def execute(self, args: str, agent: Agent) -> str:
        del args
        provider_name = getattr(getattr(agent, "provider", None), "model_name", "") or "(unset)"
        return "\n".join([
            "Version:",
            f"  ccmini: {_resolve_ccmini_version()}",
            f"  python: {sys.version.split()[0]}",
            f"  model: {provider_name}",
            f"  cwd: {os.getcwd()}",
        ])


def register_builtin_commands(registry: CommandRegistry) -> None:
    """Register all built-in slash commands."""
    review_command = Command(
        name="review",
        description="Review a pull request",
        type=CommandType.PROMPT,
        source=CommandSource.BUILTIN,
        loaded_from=CommandSource.BUILTIN,
        prompt_text=(
            "You are an expert code reviewer.\n\n"
            "Follow these steps:\n"
            "1. If no PR number is provided, use git and gh to discover the active branch or open PRs.\n"
            "2. If a PR number is provided, inspect that PR with gh.\n"
            "3. Get the diff and review the changed files.\n"
            "4. Focus on bugs, regressions, missing tests, risky behavior changes, and unclear assumptions.\n"
            "5. Keep the review concise but specific, with file paths and concrete reasoning.\n\n"
            "Preferred commands:\n"
            "- gh pr list\n"
            "- gh pr view <number>\n"
            "- gh pr diff <number>\n"
            "- git status\n"
            "- git diff --stat\n"
        ),
        when_to_use="Use when the user asks for a code review or PR review.",
        allowed_tools=["Bash", "Read", "Grep", "Glob", "LSP"],
        user_invocable=True,
        has_user_specified_description=True,
    )
    ultraplan_command = Command(
        name="ultraplan",
        description="Generate a deep implementation plan for complex work",
        type=CommandType.PROMPT,
        source=CommandSource.BUILTIN,
        loaded_from=CommandSource.BUILTIN,
        prompt_text=(
            "Create a comprehensive execution plan for the user's request.\n\n"
            "Requirements:\n"
            "1. Break the work into clear phases.\n"
            "2. Call out risks, dependencies, and assumptions.\n"
            "3. Identify what should be verified after each phase.\n"
            "4. Prefer concrete implementation steps over abstract advice.\n"
            "5. If the request is ambiguous, make the smallest safe assumption and note it.\n"
        ),
        when_to_use="Use when the user asks for an exhaustive plan or says 'ultraplan'.",
        allowed_tools=["Bash", "Read", "Grep", "Glob", "LSP", "TodoWrite"],
        user_invocable=True,
        has_user_specified_description=True,
    )
    registry.register_command(review_command)
    registry.register_command(ultraplan_command)
    registry.register(ContextCommand())
    registry.register(ThemeCommand())
    registry.register(OutputStyleCommand())
    registry.register(CompactCommand())
    registry.register(ClearCommand())
    registry.register(MemoryCommand())
    registry.register(FilesCommand())
    registry.register(KeybindingsCommand())
    registry.register(HelpCommand())
    registry.register(BriefCommand())
    registry.register(RenameCommand())
    registry.register(PlanCommand())
    registry.register(ConfigCommand())
    registry.register(LoginCommand())
    registry.register(LogoutCommand())
    registry.register(PluginCommand())
    registry.register(McpCommand())
    registry.register(HooksCommand())
    registry.register(TasksCommand())
    registry.register(SessionCommand())
    registry.register(RewindCommand())
    registry.register(AgentsCommand())
    registry.register(SkillsCommand())
    registry.register(StatusCommand())
    registry.register(StatsCommand())
    registry.register(UsageCommand())
    registry.register(PermissionsCommand())
    registry.register(DoctorCommand())
    registry.register(TerminalSetupCommand())
    registry.register(StatuslineCommand())
    registry.register(FeedbackCommand())
    registry.register(CostCommand())
    registry.register(ModelCommand())
    registry.register(VoiceCommand())
    registry.register(ExitCommand())
    registry.register(VersionCommand())


def register_bundled_skills_as_commands(registry: CommandRegistry) -> None:
    """Register bundled skills into the unified command system."""
    from ..skills.bundled import get_bundled_skills
    for skill in get_bundled_skills().values():
        registry.register_command(command_from_bundled_skill(skill))
