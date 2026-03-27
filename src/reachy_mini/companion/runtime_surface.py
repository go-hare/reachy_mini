"""Build minimal runtime-facing surface-state payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from reachy_mini.companion.models import CompanionIntent, SurfaceExpression
from reachy_mini.companion.orchestrator import build_companion_surface

if TYPE_CHECKING:
    from reachy_mini.affect import AffectState, EmotionSignal


@dataclass(slots=True, frozen=True)
class CompanionSurfaceBundle:
    """Surface package returned for one replying turn."""

    intent: CompanionIntent
    expression: SurfaceExpression
    state: dict[str, Any]


def build_listening_surface_state(
    *,
    thread_id: str,
    affect_state: "AffectState | None" = None,
    emotion_signal: "EmotionSignal | None" = None,
) -> dict[str, Any]:
    """Build the default listening state before kernel output exists."""

    state = {
        "thread_id": thread_id,
        "phase": "listening",
        "recommended_hold_ms": 0,
    }
    state.update(build_affect_payload(affect_state))
    state.update(build_emotion_payload(emotion_signal))
    return state


def build_listening_wait_surface_state(
    *,
    thread_id: str,
    affect_state: "AffectState | None" = None,
    emotion_signal: "EmotionSignal | None" = None,
) -> dict[str, Any]:
    """Build the short post-speech waiting state before replying begins."""

    state = {
        "thread_id": thread_id,
        "phase": "listening_wait",
        "recommended_hold_ms": 600,
    }
    state.update(build_affect_payload(affect_state))
    state.update(build_emotion_payload(emotion_signal))
    return state


def build_idle_surface_state(
    *,
    thread_id: str,
    affect_state: "AffectState | None" = None,
    emotion_signal: "EmotionSignal | None" = None,
) -> dict[str, Any]:
    """Build the default idle state when no expressive reply exists."""

    state = {
        "thread_id": thread_id,
        "phase": "idle",
        "recommended_hold_ms": 0,
    }
    state.update(build_affect_payload(affect_state))
    state.update(build_emotion_payload(emotion_signal))
    return state


def build_turn_surface_bundle(
    *,
    thread_id: str,
    user_text: str,
    kernel_output: str,
    affect_state: "AffectState | None" = None,
    emotion_signal: "EmotionSignal | None" = None,
) -> CompanionSurfaceBundle:
    """Build intent, expression, and replying state for one turn."""

    intent, expression = build_companion_surface(
        user_text=user_text,
        kernel_output=kernel_output,
        affect_state=affect_state,
        emotion_signal=emotion_signal,
    )
    return CompanionSurfaceBundle(
        intent=intent,
        expression=expression,
        state=build_companion_phase_surface_state(
            thread_id=thread_id,
            phase="replying",
            companion_intent=intent,
            surface_expression=expression,
            affect_state=affect_state,
            emotion_signal=emotion_signal,
        ),
    )


def build_companion_phase_surface_state(
    *,
    thread_id: str,
    phase: str,
    companion_intent: CompanionIntent,
    surface_expression: SurfaceExpression,
    affect_state: "AffectState | None" = None,
    emotion_signal: "EmotionSignal | None" = None,
) -> dict[str, Any]:
    """Build one minimal runtime-facing state for a companion phase."""

    _ = companion_intent, surface_expression
    normalized_phase = str(phase or "").strip().lower() or "idle"
    state = {
        "thread_id": thread_id,
        "phase": normalized_phase,
        "recommended_hold_ms": 900 if normalized_phase == "settling" else 0,
    }
    state.update(build_affect_payload(affect_state))
    state.update(build_emotion_payload(emotion_signal))
    return state


def build_affect_payload(affect_state: "AffectState | None") -> dict[str, Any]:
    """Convert affect runtime values into a flat surface-state payload."""

    if affect_state is None:
        return {}
    return {
        "affect_pleasure": affect_state.current_pad.pleasure,
        "affect_arousal": affect_state.current_pad.arousal,
        "affect_dominance": affect_state.current_pad.dominance,
        "affect_vitality": affect_state.vitality,
        "affect_pressure": affect_state.pressure,
        "affect_updated_at": affect_state.updated_at,
    }


def build_emotion_payload(emotion_signal: "EmotionSignal | None") -> dict[str, Any]:
    """Convert one semantic emotion signal into a flat surface-state payload."""

    if emotion_signal is None:
        return {}
    payload = emotion_signal.to_dict()
    return {
        "emotion_primary": payload["primary_emotion"],
        "emotion_intensity": payload["intensity"],
        "emotion_confidence": payload["confidence"],
        "emotion_support_need": payload["support_need"],
        "emotion_wants_action": payload["wants_action"],
        "emotion_trigger_text": payload["trigger_text"],
    }
