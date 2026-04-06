"""Terminal buddy panel — ports key constants from ``buddy/CompanionSprite.tsx``."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from .sprites import render_sprite, sprite_frame_count
from .types import Companion, CompanionBones

# From CompanionSprite.tsx
TICK_MS = 500
MIN_COLS_FOR_FULL_SPRITE = 100
SPRITE_BODY_WIDTH = 12
SPRITE_PADDING_X = 2
BUBBLE_WIDTH = 36
PET_BURST_S = 2.5
H = "♥"
PET_HEARTS = [
    f"   {H}    {H}   ",
    f"  {H}  {H}   {H}  ",
    f" {H}   {H}  {H}   ",
    f"{H}  {H}      {H} ",
    "·    ·   ·  ",
]
# Idle: mostly frame 0, occasional fidget, rare blink (-1 = blink on 0)
IDLE_SEQUENCE = (0, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 2, 0, 0, 0)


def _companion_to_bones(c: Companion) -> CompanionBones:
    return CompanionBones(
        rarity=c.rarity,
        species=c.species,
        eye=c.eye,
        hat=c.hat,
        shiny=c.shiny,
        stats=c.stats,
    )


@dataclass
class CompanionRenderState:
    """Mutable UI state for the terminal buddy panel."""

    muted: bool = False
    reaction: str | None = None
    reaction_until: float = 0.0
    pet_until: float = 0.0
    _idle_idx: int = 0
    _tick: int = 0

    def set_pet(self) -> None:
        self.pet_until = time.monotonic() + PET_BURST_S

    def set_reaction(self, text: str | None) -> None:
        self.reaction = text
        if text:
            self.reaction_until = time.monotonic() + 10.0
        else:
            self.reaction_until = 0.0

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self.set_reaction(None)

    def tick(self) -> None:
        self._tick += 1
        self._idle_idx = (self._idle_idx + 1) % len(IDLE_SEQUENCE)
        now = time.monotonic()
        if self.reaction and now > self.reaction_until:
            self.reaction = None
        if now > self.pet_until:
            self.pet_until = 0.0

    def current_sprite_frame(self, species: str) -> int:
        raw = IDLE_SEQUENCE[self._idle_idx]
        n = sprite_frame_count(species)
        if raw < 0:
            return 0
        return raw % n


def companion_reserved_columns(
    companion: Companion | None,
    terminal_columns: int,
    *,
    speaking: bool,
    muted: bool,
) -> int:
    """Width reserved for the right-side sprite column (cf. ``companionReservedColumns``)."""
    if muted or companion is None:
        return 0
    if terminal_columns < MIN_COLS_FOR_FULL_SPRITE:
        return 0
    name_w = len(companion.name)
    sprite_w = max(SPRITE_BODY_WIDTH, name_w + 2)
    bubble = BUBBLE_WIDTH if speaking else 0
    return sprite_w + SPRITE_PADDING_X + bubble


def render_companion(
    companion: Companion,
    state: CompanionRenderState,
    *,
    columns: int,
) -> str:
    """Multi-line ASCII panel for the prompt's right column."""
    bones = _companion_to_bones(companion)
    frame = state.current_sprite_frame(companion.species)
    lines = render_sprite(bones, frame)

    out: list[str] = []
    now = time.monotonic()
    if now < state.pet_until:
        phase = state._tick % len(PET_HEARTS)
        out.append(PET_HEARTS[phase])

    out.extend(lines)
    focus = f" {companion.name} "
    pad = max(0, SPRITE_BODY_WIDTH - len(focus))
    out.append(f"{' ' * (pad // 2)}{focus}{' ' * (pad - pad // 2)}")

    if state.reaction and not state.muted:
        bubble = _wrap_bubble(state.reaction, min(30, max(20, columns // 4)))
        out.extend(bubble)

    out.append(f" · {companion.rarity} · ")

    return "\n".join(out)


def _wrap_bubble(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return ["╭" + "─" * min(width, 20) + "╮", "╰" + "─" * min(width, 20) + "╯"]
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


async def run_animation_loop(
    companion: Companion,
    state: CompanionRenderState,
    *,
    on_frame: Callable[[str], None],
    columns: int,
    tick_ms: int = TICK_MS,
) -> None:
    """Drive idle animation until cancelled (cf. sprite tick in TS)."""
    try:
        while True:
            await asyncio.sleep(tick_ms / 1000.0)
            state.tick()
            text = render_companion(companion, state, columns=columns)
            on_frame(text)
    except asyncio.CancelledError:
        raise


__all__ = [
    "CompanionRenderState",
    "MIN_COLS_FOR_FULL_SPRITE",
    "companion_reserved_columns",
    "render_companion",
    "run_animation_loop",
    "TICK_MS",
]
