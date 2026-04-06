"""Lightweight nurture stats (terminal buddy) — optional persistence beside TS ``nurture``."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..paths import mini_agent_home


@dataclass
class NurtureEngine:
    """Pet / interaction counts for display and future mechanics."""

    pet_count: int = 0
    last_note: str = ""

    @classmethod
    def _path(cls) -> Path:
        return mini_agent_home() / "buddy_nurture.json"

    @classmethod
    def load(cls) -> NurtureEngine:
        path = cls._path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(
            pet_count=int(data.get("pet_count", 0)),
            last_note=str(data.get("last_note", "")),
        )

    def save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def record_pet(self) -> None:
        self.pet_count += 1
        self.save()


__all__ = ["NurtureEngine"]
