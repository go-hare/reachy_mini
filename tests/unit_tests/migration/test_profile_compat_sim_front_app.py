"""Profile compatibility regression: sim_front_app loads under v4 unchanged."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reachy_mini.pipeline.frames import BrainReplyFrame, WorkerEventFrame
from reachy_mini.pipeline.session import SURFACE_TASK_ID, RuntimeSession
from reachy_mini.reachy_brain.config import from_profile


REPO_ROOT = Path(__file__).resolve().parents[3]
SIM_FRONT_APP_PROFILE = REPO_ROOT / "profiles" / "sim_front_app" / "profiles"


_MOCK_OVERRIDES = {
    "model.provider": "mock",
    "model.model": "v4_compat_mock",
    "model.base_url": None,
    "model.api_key_ref": "",
    "model.api_key": "",
}


def test_sim_front_app_profile_loads_with_expected_fields() -> None:
    """sim_front_app profile maps cleanly into AgentConfig."""
    config = from_profile(SIM_FRONT_APP_PROFILE, overrides=dict(_MOCK_OVERRIDES))

    assert config.speech.enabled is True
    assert config.speech.provider == "kokoro"
    assert config.speech.voice == "zf_001"

    assert config.speech_input.provider == "funasr"
    assert config.speech_input.enabled is True
    assert config.speech_input.language == "zh"

    assert config.vision.no_camera is False
    assert config.vision.head_tracker == "yolo"


@pytest.mark.asyncio
async def test_sim_front_app_runtime_session_starts_and_handles_text_turn() -> None:
    """RuntimeSession.from_profile starts, runs a text turn, and stops cleanly."""
    session = RuntimeSession.from_profile(
        SIM_FRONT_APP_PROFILE,
        overrides=dict(_MOCK_OVERRIDES),
    )
    await session.start()
    sub = session.subscribe(filter=lambda frame: isinstance(frame, BrainReplyFrame))
    try:
        turn_id = await session.submit_text("你好", turn_id="T1")
        await session.wait_for_turn_idle(turn_id, timeout=3.0)
        # Drain at least one BrainReplyFrame for the turn we just submitted.
        reply = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
        assert isinstance(reply, BrainReplyFrame)
        assert reply.turn_id == "T1"
        assert reply.reply_text != ""
    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_sim_front_app_surface_state_flows_through_session() -> None:
    """Surface phase transitions reach subscribers via WorkerEventFrame(__surface__)."""
    session = RuntimeSession.from_profile(
        SIM_FRONT_APP_PROFILE,
        overrides=dict(_MOCK_OVERRIDES),
    )
    await session.start()
    sub = session.subscribe(
        filter=lambda frame: isinstance(frame, WorkerEventFrame)
        and frame.task_id == SURFACE_TASK_ID
    )
    try:
        turn_id = await session.submit_text("你好", turn_id="T1")
        await session.wait_for_turn_idle(turn_id, timeout=3.0)
        phases: list[str] = []
        deadline = asyncio.get_event_loop().time() + 1.5
        while asyncio.get_event_loop().time() < deadline:
            try:
                frame = await asyncio.wait_for(sub.queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                if phases and phases[-1] == "idle":
                    break
                continue
            phases.append(frame.payload["state"]["phase"])
            if phases[-1] == "idle" and "replying" in phases:
                break
        assert "replying" in phases
        assert phases[-1] == "idle"
    finally:
        await session.stop()
