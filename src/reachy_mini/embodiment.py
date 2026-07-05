"""Shared body-agnostic embodiment frame contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EmbodimentFrame:
    """Body-agnostic expression event for robots, 3D bodies, and avatars."""

    action: str
    payload: dict[str, Any]
    target: str = "all"
    turn_id: str = ""
    ts_ms: int = 0


__all__ = ["EmbodimentFrame"]
