"""Buddy prompt helpers aligned to the recovered reference source."""

from __future__ import annotations

from typing import Any

from .companion import _read_global_config, get_companion


def companion_intro_text(name: str, species: str) -> str:
    """Same wording as ``buddy/prompt.ts`` ``companionIntroText`` (meta system-reminder)."""
    return (
        f"# Companion\n\n"
        f"A small {species} named {name} sits beside the user's input box and occasionally comments in a speech bubble. "
        f"You're not {name} — it's a separate watcher.\n\n"
        f"When the user addresses {name} directly (by name), its bubble will answer. "
        f"Your job in that moment is to stay out of the way: respond in ONE line or less, "
        f"or just answer any part of the message meant for you. Don't explain that you're not {name} — "
        f"they know. Don't narrate what {name} might say — the bubble handles that."
    )


def _global_companion_muted() -> bool:
    """Mirror ``getGlobalConfig().companionMuted`` (``utils/config.ts``)."""
    v = _read_global_config().get("companionMuted")
    return v is True


def get_companion_intro_attachment(
    messages: list[Any] | None,
    *,
    buddy_enabled: bool = True,
    companion_muted: bool | None = None,
) -> list[dict[str, str]]:
    """Mirror ``getCompanionIntroAttachment`` in ``buddy/prompt.ts``.

    Gated like the reference: ``feature('BUDDY')`` → ``buddy_enabled``;
    ``getGlobalConfig().companionMuted`` → ``companion_muted`` or global config.
    """
    if not buddy_enabled:
        return []
    if companion_muted is None:
        companion_muted = _global_companion_muted()
    if companion_muted:
        return []

    companion = get_companion()
    if companion is None:
        return []

    for message in messages or []:
        md = getattr(message, "metadata", None) or {}
        if md.get("companion_intro_name") == companion.name:
            return []
        attachment = getattr(message, "attachment", None)
        msg_type = getattr(message, "type", "")
        if msg_type == "attachment" and isinstance(attachment, dict):
            if attachment.get("type") == "companion_intro" and attachment.get("name") == companion.name:
                return []

    return [
        {
            "type": "companion_intro",
            "name": companion.name,
            "species": companion.species,
        }
    ]


__all__ = [
    "companion_intro_text",
    "get_companion_intro_attachment",
]
