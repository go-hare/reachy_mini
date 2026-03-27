"""Companion-layer orchestration between kernel truth and outer expression."""

from reachy_mini.companion.models import CompanionIntent, SurfaceExpression
from reachy_mini.companion.orchestrator import build_companion_surface
from reachy_mini.companion.runtime_surface import (
    CompanionSurfaceBundle,
    build_affect_payload,
    build_companion_phase_surface_state,
    build_emotion_payload,
    build_idle_surface_state,
    build_listening_surface_state,
    build_listening_wait_surface_state,
    build_turn_surface_bundle,
)

__all__ = [
    "CompanionSurfaceBundle",
    "CompanionIntent",
    "SurfaceExpression",
    "build_affect_payload",
    "build_companion_phase_surface_state",
    "build_companion_surface",
    "build_emotion_payload",
    "build_idle_surface_state",
    "build_listening_surface_state",
    "build_listening_wait_surface_state",
    "build_turn_surface_bundle",
]
