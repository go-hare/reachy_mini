"""Tests for RobotRuntime visualization export helpers."""

from __future__ import annotations

import json

import pytest

from reachy_mini.robot_runtime import (
    EmbodiedIntent,
    RobotRuntime,
    RobotRuntimeConfig,
    RuntimeMode,
)
from reachy_mini.robot_runtime.adapters.mujoco import MujocoAdapter
from reachy_mini.robot_runtime.visualization import export_timeline_to_rerun


class _FakeRerun:
    """Rerun-compatible recorder used for deterministic tests."""

    def __init__(self) -> None:
        self.times: list[tuple[str, int]] = []
        self.logs: list[tuple[str, dict[str, str]]] = []

    def set_time_sequence(self, name: str, value: int) -> None:
        """Record time sequence updates."""
        self.times.append((name, value))

    def TextLog(self, text: str, *, level: str) -> dict[str, str]:
        """Return a simple payload object."""
        return {"text": text, "level": level}

    def log(self, path: str, payload: dict[str, str]) -> None:
        """Record log calls."""
        self.logs.append((path, payload))


@pytest.mark.asyncio
async def test_visualization_records_serialize_trace_events() -> None:
    """A Runtime trace can be converted into visualization records."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    intent = EmbodiedIntent(intent_type="greet", turn_id="turn_viz")

    await runtime.handle_intent(intent)
    records = runtime.visualization_records(intent_id=intent.intent_id)

    assert [record.event_type for record in records] == [
        "intent_received",
        "plan_scheduled",
        "command_resolved",
        "safety_decision",
        "command_started",
        "adapter_result",
    ]
    assert records[0].path == "robot_runtime/0001_runtime_intent_received"
    assert records[-1].trace["turn_id"] == "turn_viz"
    assert records[-1].payload["adapter_id"] == "mujoco"
    assert records[-1].to_dict()["trace"]["intent_id"] == intent.intent_id


@pytest.mark.asyncio
async def test_export_trace_to_rerun_uses_injected_recorder() -> None:
    """Rerun export works with an injected recorder and no hard dependency."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    intent = EmbodiedIntent(intent_type="greet", turn_id="turn_rerun")
    recorder = _FakeRerun()

    await runtime.handle_intent(intent)
    result = runtime.export_trace_to_rerun(
        intent_id=intent.intent_id,
        recorder=recorder,
        entity_prefix="reachy",
    )

    assert result.record_count == 6
    assert result.paths[0] == "reachy/0001_runtime_intent_received"
    assert len(recorder.times) == 6
    assert recorder.times[0][0] == "robot_time_ms"
    assert [path for path, _ in recorder.logs] == list(result.paths)
    last_payload = json.loads(recorder.logs[-1][1]["text"])
    assert last_payload["event_type"] == "adapter_result"
    assert last_payload["trace"]["turn_id"] == "turn_rerun"


@pytest.mark.asyncio
async def test_standalone_rerun_export_uses_text_records() -> None:
    """The standalone helper exports the same timeline shape."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    intent = EmbodiedIntent(intent_type="greet", turn_id="turn_standalone")
    recorder = _FakeRerun()

    await runtime.handle_intent(intent)
    timeline = runtime.trace_timeline(intent_id=intent.intent_id)
    result = export_timeline_to_rerun(timeline, recorder=recorder)

    assert result.record_count == 6
    assert recorder.logs[-1][1]["level"] == "INFO"
