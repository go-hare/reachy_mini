"""``/buddy`` slash command — hatch, pet, mute (parity with TS buddy commands)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

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
        get_companion_cb: Callable[[], Any | None] | None = None,
        hatch_companion_cb: Callable[[str | None], Any] | None = None,
        is_muted_cb: Callable[[], bool] | None = None,
        set_muted_cb: Callable[[bool], None] | None = None,
        record_pet_cb: Callable[[], dict[str, Any]] | None = None,
        get_nurture_stats_cb: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._user_id = user_id
        self._nurture = nurture
        self._on_pet = on_pet
        self._on_mute_toggle = on_mute_toggle
        self._on_refresh = on_refresh
        self._get_companion_cb = get_companion_cb
        self._hatch_companion_cb = hatch_companion_cb
        self._is_muted_cb = is_muted_cb
        self._set_muted_cb = set_muted_cb
        self._record_pet_cb = record_pet_cb
        self._get_nurture_stats_cb = get_nurture_stats_cb

    @property
    def name(self) -> str:
        return "buddy"

    @property
    def description(self) -> str:
        return "Buddy companion: hatch, pet, mute, status"

    @property
    def muted(self) -> bool:
        if self._is_muted_cb is not None:
            return bool(self._is_muted_cb())
        return _read_global_config().get("companionMuted") is True

    def _get_companion(self) -> Any | None:
        if self._get_companion_cb is not None:
            return self._get_companion_cb()
        return get_companion(self._user_id)

    def _hatch_companion(self, name: str | None) -> Any:
        if self._hatch_companion_cb is not None:
            return self._hatch_companion_cb(name)
        return hatch_companion(self._user_id, name=name)

    def _set_muted(self, muted: bool) -> None:
        if self._set_muted_cb is not None:
            self._set_muted_cb(muted)
            return
        save_global_config({"companionMuted": muted})

    def _record_pet(self) -> dict[str, Any]:
        if self._record_pet_cb is not None:
            return dict(self._record_pet_cb())
        self._nurture.record_pet()
        return {"pet_count": self._nurture.pet_count, "last_note": self._nurture.last_note}

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
            if self._get_companion() is not None:
                return "You already have a companion. (Clear `companion` in config to re-hatch.)"
            name = rest or None
            self._hatch_companion(name)
            if self._on_refresh:
                await self._on_refresh()
            c = self._get_companion()
            return f"Hatched {c.name} ({c.species}, {c.rarity})!" if c else "Hatched."

        if sub == "pet":
            c = self._get_companion()
            if c is None:
                return "No companion yet — try `/buddy hatch`."
            stats = self._record_pet()
            if self._on_pet:
                self._on_pet()
            if self._on_refresh:
                await self._on_refresh()
            return f"*pats {c.name}* (pets total: {int(stats.get('pet_count', 0))})"

        if sub == "mute":
            self._set_muted(True)
            if self._on_mute_toggle:
                self._on_mute_toggle(True)
            if self._on_refresh:
                await self._on_refresh()
            return "Companion muted (no reactions / bubble column)."

        if sub == "unmute":
            self._set_muted(False)
            if self._on_mute_toggle:
                self._on_mute_toggle(False)
            if self._on_refresh:
                await self._on_refresh()
            return "Companion unmuted."

        if sub == "status":
            c = self._get_companion()
            if c is None:
                return "No companion. Use `/buddy hatch`."
            m = "muted" if self.muted else "unmuted"
            pet_count = int((self._get_nurture_stats_cb or (lambda: {"pet_count": self._nurture.pet_count}))().get("pet_count", 0))
            return (
                f"{c.name} — {c.species} ({c.rarity}), {m}\n"
                f"personality: {c.personality}\n"
                f"pets recorded: {pet_count}"
            )

        return f"Unknown subcommand {sub!r}. Try /buddy help"


__all__ = ["BuddyCommand"]
