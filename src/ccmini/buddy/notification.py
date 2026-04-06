"""Buddy notification & trigger detection — port of ``useBuddyNotification.tsx``.

Provides:
- ``is_buddy_teaser_window()`` — True during Apr 1-7 2026 (local time)
- ``is_buddy_live()`` — True from Apr 2026 onwards
- ``rainbow_text()`` — ANSI rainbow-colored text for terminal display
- ``buddy_teaser_notification()`` — Formatted teaser string for startup
- ``find_buddy_trigger_positions()`` — Locate ``/buddy`` in user input
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import NamedTuple

# ── Rainbow colors (6-color cycle) ──────────────────────────────────

_RAINBOW_ANSI = [
    "\033[31m",  # red
    "\033[33m",  # yellow
    "\033[32m",  # green
    "\033[36m",  # cyan
    "\033[34m",  # blue
    "\033[35m",  # magenta
]
_ANSI_RESET = "\033[0m"


def _get_rainbow_color(index: int) -> str:
    return _RAINBOW_ANSI[index % len(_RAINBOW_ANSI)]


# ── Date checks ──────────────────────────────────────────────────────


def is_buddy_teaser_window() -> bool:
    """True during Apr 1-7, 2026 (local time).

    Local date (not UTC) so each timezone gets a 24h rolling teaser.
    """
    d = datetime.now()
    return d.year == 2026 and d.month == 4 and d.day <= 7


def is_buddy_live() -> bool:
    """True from April 2026 onwards."""
    d = datetime.now()
    return d.year > 2026 or (d.year == 2026 and d.month >= 4)


# ── Rainbow text ─────────────────────────────────────────────────────


def rainbow_text(text: str) -> str:
    """Return ``text`` with each character in a cycling rainbow ANSI color."""
    chars: list[str] = []
    for i, ch in enumerate(text):
        chars.append(f"{_get_rainbow_color(i)}{ch}")
    chars.append(_ANSI_RESET)
    return "".join(chars)


# ── Teaser notification ──────────────────────────────────────────────


def buddy_teaser_notification(*, has_companion: bool = False) -> str | None:
    """Return a rainbow ``/buddy`` teaser string, or None if not appropriate.

    Returns None if:
    - The user already has a companion
    - We're outside the teaser window
    """
    if has_companion or not is_buddy_teaser_window():
        return None
    return rainbow_text("/buddy")


# ── Trigger detection ────────────────────────────────────────────────


class TriggerPosition(NamedTuple):
    start: int
    end: int


_BUDDY_RE = re.compile(r"/buddy\b")


def find_buddy_trigger_positions(text: str) -> list[TriggerPosition]:
    """Find all ``/buddy`` command positions in user input text.

    Returns a list of (start, end) tuples for each ``/buddy`` occurrence.
    Used by the input renderer to highlight ``/buddy`` in rainbow colors.
    """
    return [
        TriggerPosition(start=m.start(), end=m.end())
        for m in _BUDDY_RE.finditer(text)
    ]
