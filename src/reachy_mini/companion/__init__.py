"""Companion-layer orchestration between kernel truth and outer expression."""

from reachy_mini.companion.models import CompanionIntent, SurfaceExpression
from reachy_mini.companion.orchestrator import build_companion_surface

__all__ = [
    "CompanionIntent",
    "SurfaceExpression",
    "build_companion_surface",
]
