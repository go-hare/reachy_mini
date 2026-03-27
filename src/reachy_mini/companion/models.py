"""Small companion-layer data structures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CompanionIntent:
    """How this turn should accompany the user."""

    mode: str
    warmth: float
    initiative: float
    intensity: float


@dataclass(slots=True)
class SurfaceExpression:
    """How the companion intent should look and feel externally."""

    text_style: str
    expression: str
