"""Tests for SDK message bridge helpers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdk_fakes import assistant_message, task_completed, task_progress, task_started  # noqa: E402

from reachy_mini.pipeline.frames import SDKMessageFrame  # noqa: E402
from reachy_mini.reachy_brain.pipecat_bridge import (  # noqa: E402
    extract_text_blocks,
    sdk_message_to_payload,
    sdk_message_to_speech_frames,
    sdk_message_to_worker_event,
)


def test_assistant_text_blocks_become_speech_frames() -> None:
    """AssistantMessage/TextBlock content is the only TTS source."""
    message = assistant_message("你好", session_id="T1")
    frame = SDKMessageFrame(message=message, turn_id="T1")

    speech_frames = sdk_message_to_speech_frames(frame, style={"voice": "zf_001"})

    assert extract_text_blocks(message) == ["你好"]
    assert len(speech_frames) == 1
    assert speech_frames[0].text == "你好"
    assert speech_frames[0].turn_id == "T1"
    assert speech_frames[0].style["voice"] == "zf_001"


def test_sdk_task_messages_become_worker_events() -> None:
    """SDK task/subagent messages map to observable WorkerEventFrame events."""
    started = sdk_message_to_worker_event(
        SDKMessageFrame(message=task_started("w1"), turn_id="T1")
    )
    progress = sdk_message_to_worker_event(
        SDKMessageFrame(message=task_progress("w1"), turn_id="T1")
    )
    completed = sdk_message_to_worker_event(
        SDKMessageFrame(message=task_completed("w1"), turn_id="T1")
    )

    assert started is not None
    assert started.task_id == "w1"
    assert started.event == "started"
    assert progress is not None
    assert progress.event == "progress"
    assert completed is not None
    assert completed.event == "completed"


def test_sdk_message_payload_preserves_message_type() -> None:
    """Wire payload serialization keeps the SDK message type visible."""
    payload = sdk_message_to_payload(assistant_message("hi"))

    assert payload["message_type"] == "AssistantMessage"
    assert payload["content"][0]["text"] == "hi"
