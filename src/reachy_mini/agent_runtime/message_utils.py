"""Helpers for extracting text from chat model messages."""

from __future__ import annotations

from typing import Any


def extract_message_text(message: Any) -> str:
    """Normalize supported LangChain message payloads into plain text."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()
