"""Output format system — render agent content in multiple styles.

Ported from Claude Code's ``outputStyles/loadOutputStylesDir`` pattern:
- Enum of built-in styles (MARKDOWN, JSON, PLAIN, COMPACT, STRUCTURED)
- ``StyleRenderer`` applies formatting to content, tool results, errors, code, diffs
- Custom style definitions loadable from ``~/.mini-agent/styles/``
- Terminal capability detection to adapt styles for limited environments
"""

from __future__ import annotations

import enum
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Output style enum ───────────────────────────────────────────────

class OutputStyle(enum.Enum):
    """Built-in output format styles."""

    MARKDOWN = "markdown"
    JSON = "json"
    PLAIN = "plain"
    COMPACT = "compact"
    STRUCTURED = "structured"


# ── Configuration ───────────────────────────────────────────────────

@dataclass(slots=True)
class OutputStyleConfig:
    """Per-context style selections."""

    default_style: OutputStyle = OutputStyle.MARKDOWN
    stream_style: OutputStyle = OutputStyle.MARKDOWN
    error_style: OutputStyle = OutputStyle.PLAIN


@dataclass(slots=True)
class TerminalCapabilities:
    """Detected terminal feature set."""

    color_support: bool = True
    true_color: bool = False
    width: int = 80
    height: int = 24
    unicode_support: bool = True
    is_tty: bool = True


@dataclass(slots=True)
class CustomStyle:
    """A user-defined output style loaded from disk."""

    name: str
    description: str
    prompt: str
    source: str = ""
    keep_coding_instructions: bool | None = None


# ── Terminal detection ──────────────────────────────────────────────

def get_terminal_capabilities() -> TerminalCapabilities:
    """Detect the current terminal's capabilities."""
    is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    size = shutil.get_terminal_size(fallback=(80, 24))

    color = is_tty
    true_color = False
    if is_tty:
        colorterm = os.environ.get("COLORTERM", "").lower()
        true_color = colorterm in ("truecolor", "24bit")
        if not color:
            term = os.environ.get("TERM", "")
            color = "color" in term or "256" in term

    unicode_ok = True
    if is_tty:
        lang = os.environ.get("LANG", "") + os.environ.get("LC_ALL", "")
        if lang and "utf" not in lang.lower():
            unicode_ok = False

    return TerminalCapabilities(
        color_support=color,
        true_color=true_color,
        width=size.columns,
        height=size.lines,
        unicode_support=unicode_ok,
        is_tty=is_tty,
    )


def adapt_style_to_terminal(
    style: OutputStyle,
    capabilities: TerminalCapabilities,
) -> OutputStyle:
    """Downgrade a style when the terminal cannot handle it.

    - Non-TTY → PLAIN (no colour escapes or box drawing)
    - Very narrow terminal → COMPACT
    """
    if not capabilities.is_tty:
        return OutputStyle.PLAIN
    if capabilities.width < 40:
        return OutputStyle.COMPACT
    return style


# ── Core renderer ───────────────────────────────────────────────────

class StyleRenderer:
    """Render content according to an :class:`OutputStyle`."""

    def __init__(self, config: OutputStyleConfig | None = None) -> None:
        self._config = config or OutputStyleConfig()
        self._custom_styles: dict[str, CustomStyle] = {}

    @property
    def config(self) -> OutputStyleConfig:
        return self._config

    def register_custom_style(self, style: CustomStyle) -> None:
        self._custom_styles[style.name] = style

    # ── public render methods ───────────────────────────────────────

    def render(self, content: str, style: OutputStyle | None = None) -> str:
        """Format arbitrary *content* in the given style."""
        style = style or self._config.default_style
        return _RENDERERS[style](content)

    def render_tool_result(
        self,
        tool_name: str,
        result: str,
        style: OutputStyle | None = None,
    ) -> str:
        """Format a tool execution result."""
        style = style or self._config.default_style
        if style is OutputStyle.JSON:
            return json.dumps(
                {"tool": tool_name, "result": result}, ensure_ascii=False,
            )
        if style is OutputStyle.COMPACT:
            one_line = result.replace("\n", " ")[:120]
            return f"[{tool_name}] {one_line}"
        if style is OutputStyle.STRUCTURED:
            return f"── {tool_name} ──\n{result}\n{'─' * (len(tool_name) + 6)}"
        if style is OutputStyle.PLAIN:
            return f"{tool_name}: {result}"
        # MARKDOWN (default)
        return f"**{tool_name}**\n```\n{result}\n```"

    def render_error(
        self, error: str | Exception, style: OutputStyle | None = None,
    ) -> str:
        """Format an error message."""
        style = style or self._config.error_style
        msg = str(error)
        if style is OutputStyle.JSON:
            return json.dumps({"error": msg}, ensure_ascii=False)
        if style is OutputStyle.COMPACT:
            return f"ERR: {msg[:120]}"
        if style is OutputStyle.STRUCTURED:
            return f"┌ ERROR\n│ {msg}\n└───────"
        if style is OutputStyle.PLAIN:
            return f"Error: {msg}"
        return f"> **Error:** {msg}"

    def render_code(
        self,
        code: str,
        language: str = "",
        style: OutputStyle | None = None,
    ) -> str:
        """Format a code block."""
        style = style or self._config.default_style
        if style is OutputStyle.JSON:
            return json.dumps(
                {"code": code, "language": language}, ensure_ascii=False,
            )
        if style is OutputStyle.COMPACT:
            preview = code.split("\n", 1)[0][:80]
            return f"[{language}] {preview}..."
        if style is OutputStyle.PLAIN:
            return code
        if style is OutputStyle.STRUCTURED:
            border = "═" * max(40, min(80, max((len(l) for l in code.splitlines()), default=40)))
            return f"╔{border}╗\n{code}\n╚{border}╝"
        return f"```{language}\n{code}\n```"

    def render_diff(
        self, diff_text: str, style: OutputStyle | None = None,
    ) -> str:
        """Format a diff."""
        style = style or self._config.default_style
        if style is OutputStyle.JSON:
            return json.dumps({"diff": diff_text}, ensure_ascii=False)
        if style is OutputStyle.COMPACT:
            added = sum(1 for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))
            return f"[diff +{added}/-{removed}]"
        if style is OutputStyle.PLAIN:
            return diff_text
        return f"```diff\n{diff_text}\n```"

    def render_progress(
        self,
        percent: float,
        message: str = "",
        style: OutputStyle | None = None,
    ) -> str:
        """Format a progress indicator."""
        style = style or self._config.stream_style
        pct = max(0.0, min(100.0, percent))
        if style is OutputStyle.JSON:
            return json.dumps(
                {"progress": pct, "message": message}, ensure_ascii=False,
            )
        if style is OutputStyle.COMPACT:
            return f"[{pct:.0f}%] {message}"

        bar_width = 20
        filled = int(bar_width * pct / 100)
        bar = "█" * filled + "░" * (bar_width - filled)
        return f"[{bar}] {pct:.0f}%  {message}"


# ── Per-style passthrough renderers ─────────────────────────────────

def _render_markdown(content: str) -> str:
    return content


def _render_json(content: str) -> str:
    return json.dumps({"content": content}, ensure_ascii=False)


def _render_plain(content: str) -> str:
    return content


def _render_compact(content: str) -> str:
    lines = content.splitlines()
    if len(lines) <= 5:
        return content
    return "\n".join(lines[:3]) + f"\n... ({len(lines) - 3} more lines)"


def _render_structured(content: str) -> str:
    return content


_RENDERERS: dict[OutputStyle, Any] = {
    OutputStyle.MARKDOWN: _render_markdown,
    OutputStyle.JSON: _render_json,
    OutputStyle.PLAIN: _render_plain,
    OutputStyle.COMPACT: _render_compact,
    OutputStyle.STRUCTURED: _render_structured,
}


# ── Custom style loading ───────────────────────────────────────────

def _styles_dir() -> Path:
    from .config import _home_dir
    return _home_dir() / "styles"


def load_custom_styles(dir_path: str | Path | None = None) -> list[CustomStyle]:
    """Load custom output style definitions from a directory.

    Each ``.md`` file becomes a custom style. The filename (minus extension)
    is the style name; the file content becomes the style prompt.

    Mirrors Claude Code's ``loadOutputStylesDir`` behaviour: files are plain
    Markdown whose content is used verbatim as a prompt overlay.

    Parameters
    ----------
    dir_path:
        Override directory. Defaults to ``~/.mini-agent/styles/``.
    """
    d = Path(dir_path) if dir_path else _styles_dir()
    if not d.is_dir():
        return []

    styles: list[CustomStyle] = []
    for md_file in sorted(d.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
            name = md_file.stem
            description, prompt = _parse_style_file(text, name)
            styles.append(CustomStyle(
                name=name,
                description=description,
                prompt=prompt,
                source=str(md_file),
            ))
        except Exception:
            logger.debug("Failed to load custom style %s", md_file, exc_info=True)

    return styles


def _parse_style_file(text: str, fallback_name: str) -> tuple[str, str]:
    """Extract description (first line) and prompt (rest) from a style file."""
    lines = text.strip().splitlines()
    if not lines:
        return f"Custom {fallback_name} output style", ""

    first = lines[0].strip()
    if first.startswith("#"):
        description = first.lstrip("#").strip()
        prompt = "\n".join(lines[1:]).strip()
    else:
        description = first[:100]
        prompt = text.strip()

    return description or f"Custom {fallback_name} output style", prompt


# ── Module-level convenience ────────────────────────────────────────

_default_renderer: StyleRenderer | None = None


def get_renderer() -> StyleRenderer:
    """Return (or create) the module-level default renderer."""
    global _default_renderer
    if _default_renderer is None:
        _default_renderer = StyleRenderer()
    return _default_renderer


def set_default_config(config: OutputStyleConfig) -> None:
    """Replace the module-level renderer's config."""
    global _default_renderer
    _default_renderer = StyleRenderer(config)
