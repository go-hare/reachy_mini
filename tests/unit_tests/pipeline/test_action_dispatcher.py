"""Tests for v4 ActionDispatcher."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from reachy_mini.action_runtime import ActionExecutor, ActionSpec
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher
from reachy_mini.pipeline.frames import ActionResultFrame, ActionSpecFrame, BrainReplyFrame, InterruptFrame


class FakeMini:
    """Fake SDK object for dispatcher tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def goto_target(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def get_present_antenna_joint_positions(self) -> list[float]:
        return [0.0, 0.0]


@pytest.mark.asyncio
async def test_dispatcher_unpacks_brain_actions() -> None:
    """BrainReplyFrame actions become ActionSpecFrame objects."""
    dispatcher = ActionDispatcher(
        ActionExecutor(registry=create_builtin_registry(), mini=FakeMini())
    )
    spec = ActionSpec(name="nod", owner_id="main-agent")

    frames = await dispatcher.process(
        BrainReplyFrame(reply_text="hi", actions=[spec], turn_id="t1")
    )

    assert frames == [ActionSpecFrame(spec=spec, turn_id="t1")]


@pytest.mark.asyncio
async def test_dispatcher_executes_action_spec_frame() -> None:
    """ActionSpecFrame runs through ActionExecutor."""
    dispatcher = ActionDispatcher(
        ActionExecutor(registry=create_builtin_registry(), mini=FakeMini())
    )

    frames = await dispatcher.process(
        ActionSpecFrame(
            spec=ActionSpec(
                name="set_antenna",
                params={"left_deg": 20},
                owner_id="main-agent",
                request_id="r1",
            ),
            turn_id="t1",
        )
    )

    assert len(frames) == 1
    assert isinstance(frames[0], ActionResultFrame)
    assert frames[0].request_id == "r1"
    assert frames[0].status == "ok"


@pytest.mark.asyncio
async def test_dispatcher_returns_error_frame_for_invalid_action_spec() -> None:
    """Invalid specs are rejected as observable error frames."""
    dispatcher = ActionDispatcher(
        ActionExecutor(registry=create_builtin_registry(), mini=FakeMini())
    )

    frames = await dispatcher.process(
        ActionSpecFrame(
            spec=ActionSpec(name="nod", owner_id="", request_id="r-empty-owner"),
            turn_id="t1",
        )
    )

    assert len(frames) == 1
    assert isinstance(frames[0], ActionResultFrame)
    assert frames[0].status == "error"
    assert "owner_id" in str(frames[0].error)


@pytest.mark.asyncio
async def test_dispatcher_interrupt_cancels_running_actions() -> None:
    """Action interrupt calls executor.cancel for running requests."""
    executor = ActionExecutor(registry=create_builtin_registry(), mini=FakeMini())
    dispatcher = ActionDispatcher(executor)

    started = asyncio.create_task(
        dispatcher.process(
            ActionSpecFrame(
                spec=ActionSpec(
                    name="nod",
                    params={"cycles": 5, "period_s": 1.5},
                    owner_id="main-agent",
                    request_id="r1",
                ),
                turn_id="t1",
            )
        )
    )
    await asyncio.sleep(0.01)
    await dispatcher.process(InterruptFrame(scope="actions", reason="test"))
    frames = await asyncio.wait_for(started, timeout=2.0)

    assert isinstance(frames[0], ActionResultFrame)
    assert frames[0].status in {"cancelled", "ok"}
