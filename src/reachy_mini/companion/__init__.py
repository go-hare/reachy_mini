"""Companion-layer helpers between kernel truth and outer expression."""

from reachy_mini.companion.expression import build_surface_expression
from reachy_mini.companion.intent import build_companion_intent
from reachy_mini.companion.models import CompanionIntent, SurfaceExpression
from reachy_mini.companion.runtime_surface import (
    build_affect_payload,
    build_companion_phase_surface_state,
    build_emotion_payload,
    build_idle_surface_state,
    build_listening_surface_state,
    build_listening_wait_surface_state,
)

__all__ = [
    "CompanionIntent",
    "SurfaceExpression",
    "build_affect_payload",
    "build_companion_intent",
    "build_companion_phase_surface_state",
    "build_emotion_payload",
    "build_idle_surface_state",
    "build_listening_surface_state",
    "build_listening_wait_surface_state",
    "build_surface_expression",
]
