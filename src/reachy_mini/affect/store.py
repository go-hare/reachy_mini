"""Filesystem store for the shared affect state."""

from __future__ import annotations

import json
from pathlib import Path

from reachy_mini.affect.models import AffectState
from reachy_mini.utils.helpers import ensure_dir


class AffectStateStore:
    """Persist affect state under profile_root/memory/affect_state.json."""

    def __init__(self, profile_root: Path) -> None:
        self.profile_root = profile_root
        self.path = self.profile_root / "memory" / "affect_state.json"

    def ensure(self) -> AffectState:
        state = self.load()
        if not self.path.exists():
            self.save(state)
        return state

    def load(self) -> AffectState:
        if not self.path.exists():
            return AffectState.default()

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return AffectState.default()
        return AffectState.from_dict(payload)

    def save(self, state: AffectState) -> None:
        ensure_dir(self.path.parent)
        self.path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
