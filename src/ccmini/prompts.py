"""System prompt container used by the runtime query pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class PromptSection:
    key: str
    content: str | Callable[[], str | None]
    is_static: bool = True
    cacheable: bool = True


class SystemPrompt:
    """Composable system prompt with static/dynamic sections.

    Usage::

        sp = SystemPrompt()
        sp.add_static("You are a helpful robot assistant.")
        sp.add_static("## EXPRESSION\\nBe friendly and concise.")
        sp.add_dynamic("memory", lambda: memory_adapter.build_memory_section(...))
        sp.add_dynamic("env", lambda: f"Current time: {datetime.now()}")

        # At query time:
        text = sp.render()
    """

    def __init__(self) -> None:
        self._sections: list[PromptSection] = []

    def add_static(self, content: str, *, key: str = "", cacheable: bool = True) -> None:
        """Add a static section that never changes between turns."""
        if not content.strip():
            return
        actual_key = key or f"static_{len(self._sections)}"
        self._sections.append(PromptSection(
            key=actual_key,
            content=content,
            is_static=True,
            cacheable=cacheable,
        ))

    def add_dynamic(
        self,
        key: str,
        provider: Callable[[], str | None],
        *,
        cacheable: bool = False,
    ) -> None:
        """Add a dynamic section rebuilt each turn.

        The provider callable returns the section content or None to skip.
        """
        self._sections.append(PromptSection(
            key=key,
            content=provider,
            is_static=False,
            cacheable=cacheable,
        ))

    def render(self) -> str:
        """Render the full system prompt as a single string."""
        parts: list[str] = []
        for section in self._sections:
            if section.is_static:
                text = section.content if isinstance(section.content, str) else ""
            else:
                assert callable(section.content)
                result = section.content()
                text = result if result else ""
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)

    def clone(self) -> "SystemPrompt":
        """Return a shallow copy preserving section order and callables."""
        cloned = SystemPrompt()
        cloned._sections = list(self._sections)
        return cloned

    def render_with_cache_markers(self) -> list[dict[str, Any]]:
        """Render as Anthropic cache-control content blocks.

        Returns a list of ``{"type": "text", "text": ..., "cache_control": ...}``
        blocks suitable for the Anthropic ``system`` parameter.
        """
        blocks: list[dict[str, Any]] = []
        static_parts: list[str] = []

        for section in self._sections:
            if section.is_static:
                text = section.content if isinstance(section.content, str) else ""
                if text.strip():
                    static_parts.append(text.strip())
            else:
                if static_parts:
                    blocks.append({
                        "type": "text",
                        "text": "\n\n".join(static_parts),
                        "cache_control": {"type": "ephemeral"},
                    })
                    static_parts.clear()

                assert callable(section.content)
                result = section.content()
                if result and result.strip():
                    block: dict[str, Any] = {
                        "type": "text",
                        "text": result.strip(),
                    }
                    if section.cacheable:
                        block["cache_control"] = {"type": "ephemeral"}
                    blocks.append(block)

        if static_parts:
            blocks.append({
                "type": "text",
                "text": "\n\n".join(static_parts),
                "cache_control": {"type": "ephemeral"},
            })

        return blocks
