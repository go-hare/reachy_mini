"""Tests for companion-owned runtime surface-state helpers."""

from types import SimpleNamespace

from reachy_mini.companion import build_companion_intent, build_surface_expression
from reachy_mini.companion.runtime_surface import (
    build_companion_phase_surface_state,
    build_idle_surface_state,
    build_listening_surface_state,
    build_listening_wait_surface_state,
)


def _fake_affect_state() -> SimpleNamespace:
    return SimpleNamespace(
        current_pad=SimpleNamespace(
            pleasure=-0.2,
            arousal=0.3,
            dominance=0.1,
        ),
        vitality=0.55,
        pressure=0.42,
        updated_at=123.45,
    )


def _fake_emotion_signal() -> SimpleNamespace:
    return SimpleNamespace(
        support_need="comfort",
        confidence=0.8,
        intensity=0.7,
        wants_action=True,
        primary_emotion="anxious",
        to_dict=lambda: {
            "primary_emotion": "anxious",
            "intensity": 0.7,
            "confidence": 0.8,
            "support_need": "comfort",
            "wants_action": True,
            "trigger_text": "帮我看看日志",
        }
    )


def test_build_listening_surface_state_includes_affect_and_emotion() -> None:
    """Listening state should stay lightweight while carrying affect and emotion."""

    state = build_listening_surface_state(
        thread_id="app:main",
        affect_state=_fake_affect_state(),
        emotion_signal=_fake_emotion_signal(),
    )

    assert state["phase"] == "listening"
    assert state["recommended_hold_ms"] == 0
    assert state["affect_pressure"] == 0.42
    assert state["emotion_primary"] == "anxious"


def test_build_idle_surface_state_returns_default_quiet_idle() -> None:
    """Default idle state should remain minimal."""

    state = build_idle_surface_state(thread_id="app:main")

    assert state["phase"] == "idle"
    assert state["recommended_hold_ms"] == 0


def test_build_listening_wait_surface_state_returns_non_listening_hold() -> None:
    """Listening-wait should express only a short runtime hold."""

    state = build_listening_wait_surface_state(thread_id="app:main")

    assert state["phase"] == "listening_wait"
    assert state["recommended_hold_ms"] == 600


def test_companion_intent_and_expression_stay_lightweight() -> None:
    """Intent and expression helpers should stay lightweight and direct."""

    intent = build_companion_intent(
        user_text="帮我看看日志",
        kernel_output="需要先查看日志文件",
        affect_state=_fake_affect_state(),
        emotion_signal=_fake_emotion_signal(),
    )
    expression = build_surface_expression(intent, affect_state=_fake_affect_state())

    assert intent.mode == "comfort"
    assert expression.text_style == "soft_wrap"
    assert expression.expression == "gentle_caring"


def test_build_companion_phase_surface_state_overrides_settling_and_idle_motion() -> None:
    """Settling and idle phases should only affect the runtime hold."""

    settling = build_companion_phase_surface_state(
        thread_id="app:main",
        phase="settling",
    )
    idle = build_companion_phase_surface_state(
        thread_id="app:main",
        phase="idle",
    )

    assert settling["phase"] == "settling"
    assert settling["recommended_hold_ms"] == 900

    assert idle["phase"] == "idle"
    assert idle["recommended_hold_ms"] == 0
