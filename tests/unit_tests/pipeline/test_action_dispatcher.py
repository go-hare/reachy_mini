"""Tests for v4 ActionDispatcher."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from reachy_mini.action_runtime import ActionExecutor, ActionSpec
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher
from reachy_mini.pipeline.frames import ActionResultFrame, InterruptFrame


class FakeMini:
    """Fake SDK object for dispatcher tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def goto_target(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def get_present_antenna_joint_positions(self) -> list[float]:
        return [0.0, 0.0]


@pytest.mark.asyncio
async def test_dispatcher_run_action_executes_and_publishes_result_frame() -> None:
    """SDK MCP action facade runs ActionExecutor and emits ActionResultFrame."""
    published: list[ActionResultFrame] = []

    async def publish(frame: ActionResultFrame) -> None:
        published.append(frame)

    dispatcher = ActionDispatcher(
        ActionExecutor(registry=create_builtin_registry(), mini=FakeMini()),
        publish=publish,
    )

    result = await dispatcher.run_action(
        ActionSpec(
            name="set_antenna",
            params={"left_deg": 20},
            owner_id="main-agent",
            request_id="r1",
        )
    )

    assert result.status == "ok"
    assert len(published) == 1
    assert published[0].request_id == "r1"
    assert published[0].name == "set_antenna"
    assert published[0].owner_id == "main-agent"


@pytest.mark.asyncio
async def test_dispatcher_run_action_rejects_invalid_spec() -> None:
    """Invalid specs fail through the ActionExecutor boundary."""
    dispatcher = ActionDispatcher(
        ActionExecutor(registry=create_builtin_registry(), mini=FakeMini())
    )

    with pytest.raises(ValueError, match="owner_id"):
        await dispatcher.run_action(
            ActionSpec(name="nod", owner_id="", request_id="r-empty-owner")
        )


@pytest.mark.asyncio
async def test_dispatcher_interrupt_cancels_running_actions() -> None:
    """Action interrupt calls executor.cancel for running requests."""
    executor = ActionExecutor(registry=create_builtin_registry(), mini=FakeMini())
    dispatcher = ActionDispatcher(executor)

    started = asyncio.create_task(
        dispatcher.run_action(
            ActionSpec(
                name="nod",
                params={"cycles": 5, "period_s": 1.5},
                owner_id="main-agent",
                request_id="r1",
            )
        )
    )
    await asyncio.sleep(0.01)
    await dispatcher.process(InterruptFrame(scope="actions", reason="test"))
    result = await asyncio.wait_for(started, timeout=2.0)

    assert result.status in {"cancelled", "ok"}
