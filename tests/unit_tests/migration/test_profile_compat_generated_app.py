"""Profile compatibility regression: a freshly generated app loads under v4."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reachy_mini.pipeline.frames import SDKMessageFrame
from reachy_mini.pipeline.session import RuntimeSession
from reachy_mini.reachy_brain.offline_sdk_client import OfflineSDKClient
from reachy_mini.reachy_brain.config import from_profile
from reachy_mini.runtime.project import create_app_project


def test_generated_profile_loads_with_default_mock_model(tmp_path: Path) -> None:
    """A profile produced by `reachy-mini-agent create` loads without overrides."""
    project_root = tmp_path / "probe_app"
    create_app_project(project_root, "probe_app")
    profile_dir = project_root / "profiles"

    config = from_profile(profile_dir)

    assert config.model.provider == "mock"
    assert config.speech.enabled is False
    assert config.speech_input.enabled is False
    assert config.vision.no_camera is False


@pytest.mark.asyncio
async def test_generated_profile_runtime_session_runs_text_turn(tmp_path: Path) -> None:
    """A generated profile drives a full RuntimeSession turn end to end."""
    project_root = tmp_path / "probe_app"
    create_app_project(project_root, "probe_app")
    profile_dir = project_root / "profiles"

    session = RuntimeSession.from_profile(
        profile_dir,
        client_factory=lambda options: OfflineSDKClient(options),
    )
    await session.start()
    sub = session.subscribe(filter=lambda frame: isinstance(frame, SDKMessageFrame))
    try:
        turn_id = await session.submit_text("ping", turn_id="T1")
        await session.wait_for_turn_idle(turn_id, timeout=3.0)
        reply = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
        assert isinstance(reply, SDKMessageFrame)
        assert reply.turn_id == "T1"
        assert type(reply.message).__name__ == "AssistantMessage"
    finally:
        await session.stop()
