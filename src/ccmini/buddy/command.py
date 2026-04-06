"""``/buddy`` slash command — hatch, pet, mute (parity with TS buddy commands)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Awaitable

from ..commands import SlashCommand
from ..config import save_global_config
from .companion import _read_global_config, get_companion, hatch_companion
from .nurture import NurtureEngine

if TYPE_CHECKING:
    from ..agent import Agent


class BuddyCommand(SlashCommand):
    def __init__(
        self,
        *,
        user_id: str,
        nurture: NurtureEngine,
        on_pet: Callable[[], None] | None = None,
        on_mute_toggle: Callable[[bool], None] | None = None,
        on_refresh: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._user_id = user_id
        self._nurture = nurture
        self._on_pet = on_pet
        self._on_mute_toggle = on_mute_toggle
        self._on_refresh = on_refresh

    @property
    def name(self) -> str:
        return "buddy"

    @property
    def description(self) -> str:
        return "Buddy companion: hatch, pet, mute, status"

    @property
    def muted(self) -> bool:
        return _read_global_config().get("companionMuted") is True

    async def execute(self, args: str, agent: Agent) -> str:
        parts = args.strip().split(maxsplit=1)
        sub = (parts[0] if parts else "").lower().strip()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("", "help", "-h", "--help"):
            return (
                "Commands:\n"
                "  /buddy hatch [name] — create a companion (random appearance)\n"
                "  /buddy pet — pet your companion\n"
                "  /buddy mute|unmute — silence bubbles / reactions\n"
                "  /buddy status — show companion info"
            )

        if sub == "hatch":
            if get_companion(self._user_id) is not None:
                return "You already have a companion. (Clear `companion` in config to re-hatch.)"
            name = rest or None
            hatch_companion(self._user_id, name=name)
            if self._on_refresh:
                await self._on_refresh()
            c = get_companion(self._user_id)
            return f"Hatched {c.name} ({c.species}, {c.rarity})!" if c else "Hatched."

        if sub == "pet":
            c = get_companion(self._user_id)
            if c is None:
                return "No companion yet — try `/buddy hatch`."
            self._nurture.record_pet()
            if self._on_pet:
                self._on_pet()
            if self._on_refresh:
                await self._on_refresh()
            return f"*pats {c.name}* (pets total: {self._nurture.pet_count})"

        if sub == "mute":
            save_global_config({"companionMuted": True})
            if self._on_mute_toggle:
                self._on_mute_toggle(True)
            if self._on_refresh:
                await self._on_refresh()
            return "Companion muted (no reactions / bubble column)."

        if sub == "unmute":
            save_global_config({"companionMuted": False})
            if self._on_mute_toggle:
                self._on_mute_toggle(False)
            if self._on_refresh:
                await self._on_refresh()
            return "Companion unmuted."

        if sub == "status":
            c = get_companion(self._user_id)
            if c is None:
                return "No companion. Use `/buddy hatch`."
            m = "muted" if self.muted else "unmuted"
            return (
                f"{c.name} — {c.species} ({c.rarity}), {m}\n"
                f"personality: {c.personality}\n"
                f"pets recorded: {self._nurture.pet_count}"
            )

        return f"Unknown subcommand {sub!r}. Try /buddy help"


__all__ = ["BuddyCommand"]
