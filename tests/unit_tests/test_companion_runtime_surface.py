"""Tests for companion-owned runtime surface-state helpers."""

from types import SimpleNamespace

from reachy_mini.companion.runtime_surface import (
    build_companion_phase_surface_state,
    build_idle_surface_state,
    build_listening_surface_state,
    build_listening_wait_surface_state,
    build_turn_surface_bundle,
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
    """Listening state should be companion-owned and carry semantic payloads."""

    state = build_listening_surface_state(
        thread_id="app:main",
        affect_state=_fake_affect_state(),
        emotion_signal=_fake_emotion_signal(),
    )

    assert state["phase"] == "listening"
    assert state["motion_hint"] == "small_nod"
    assert state["affect_pressure"] == 0.42
    assert state["emotion_primary"] == "anxious"


def test_build_idle_surface_state_returns_default_quiet_idle() -> None:
    """Default idle state should stay in a quiet beside posture."""

    state = build_idle_surface_state(thread_id="app:main")

    assert state["phase"] == "idle"
    assert state["body_state"] == "resting_beside"
    assert state["motion_hint"] == "minimal"
    assert state["recommended_hold_ms"] == 0


def test_build_listening_wait_surface_state_returns_non_listening_hold() -> None:
    """Listening-wait should keep presence without freezing antennas as active listening."""

    state = build_listening_wait_surface_state(thread_id="app:main")

    assert state["phase"] == "listening_wait"
    assert state["motion_hint"] == "stay_close"
    assert state["body_state"] == "steady_listening"


def test_build_turn_surface_bundle_builds_replying_state_from_companion_rules() -> None:
    """Replying turn bundle should return intent, expression, and replying state together."""

    bundle = build_turn_surface_bundle(
        thread_id="app:main",
        user_text="帮我看看日志",
        kernel_output="需要先查看日志文件",
        affect_state=_fake_affect_state(),
        emotion_signal=_fake_emotion_signal(),
    )

    assert bundle.intent.mode == "comfort"
    assert bundle.expression.motion_hint in {"small_tilt", "stay_close"}
    assert bundle.state["phase"] == "replying"
    assert bundle.state["mode"] == bundle.intent.mode
    assert bundle.state["emotion_support_need"] == "comfort"


def test_build_companion_phase_surface_state_overrides_settling_and_idle_motion() -> None:
    """Settling and idle phases should override motion semantics without changing intent."""

    bundle = build_turn_surface_bundle(
        thread_id="app:main",
        user_text="帮我看看日志",
        kernel_output="需要先查看日志文件",
    )

    settling = build_companion_phase_surface_state(
        thread_id="app:main",
        phase="settling",
        companion_intent=bundle.intent,
        surface_expression=bundle.expression,
    )
    idle = build_companion_phase_surface_state(
        thread_id="app:main",
        phase="idle",
        companion_intent=bundle.intent,
        surface_expression=bundle.expression,
    )

    assert settling["phase"] == "settling"
    assert settling["motion_hint"] == "stay_close"
    assert settling["lifecycle_phase"] == bundle.expression.settling_phase
    assert settling["recommended_hold_ms"] == 900

    assert idle["phase"] == "idle"
    assert idle["motion_hint"] == "minimal"
    assert idle["lifecycle_phase"] == bundle.expression.idle_phase
