"""Tests for built-in v4 robot actions."""

from __future__ import annotations

import pytest

from reachy_mini.action_runtime import ActionExecutor, ActionSpec, UnknownActionError
from reachy_mini.action_runtime.library import create_builtin_registry, create_live2d_registry
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher
from reachy_mini.pipeline.frames import ActionResultFrame, EmbodimentFrame
from reachy_mini.runtime.live2d_avatar import Live2DCapabilities


class FakeMini:
    """Small fake SDK object for built-in action tests."""

    def __init__(self) -> None:
        """Track SDK calls."""
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.antennas = [0.0, 0.0]

    def goto_target(self, **kwargs: object) -> None:
        """Record a goto command."""
        self.calls.append(("goto_target", kwargs))
        if "antennas" in kwargs:
            self.antennas = list(kwargs["antennas"])  # type: ignore[arg-type]

    def look_at_world(
        self,
        x: float,
        y: float,
        z: float,
        duration: float,
        perform_movement: bool,
    ) -> object:
        """Record a look-at command."""
        self.calls.append(
            (
                "look_at_world",
                {
                    "x": x,
                    "y": y,
                    "z": z,
                    "duration": duration,
                    "perform_movement": perform_movement,
                },
            )
        )
        return object()

    def get_present_antenna_joint_positions(self) -> list[float]:
        """Return current fake antenna positions."""
        return list(self.antennas)


@pytest.mark.asyncio
async def test_executor_runs_nod_on_fake_mini() -> None:
    """Nod can run without LLM or hardware."""
    mini = FakeMini()
    executor = ActionExecutor(registry=create_builtin_registry(), mini=mini)

    result = await executor.submit(
        ActionSpec(
            name="nod",
            params={"cycles": 1, "period_s": 0.3},
            owner_id="manual-test",
        )
    )

    assert result.status == "ok"
    assert [name for name, _ in mini.calls] == ["goto_target", "goto_target", "goto_target"]


@pytest.mark.asyncio
async def test_executor_runs_set_antenna_on_fake_mini() -> None:
    """set_antenna narrows locks and converts degrees to radians."""
    mini = FakeMini()
    registry = create_builtin_registry()
    action = registry.build(
        ActionSpec(name="set_antenna", params={"left_deg": 90}, owner_id="manual-test")
    )
    executor = ActionExecutor(registry=registry, mini=mini)

    result = await executor.submit(
        ActionSpec(
            name="set_antenna",
            params={"left_deg": 90},
            owner_id="manual-test",
        )
    )

    assert action.required_locks == {"antenna_left"}
    assert result.status == "ok"
    assert pytest.approx(mini.antennas[1]) == 1.5707963267948966


@pytest.mark.asyncio
async def test_head_and_antenna_actions_can_run_independently() -> None:
    """Head and antenna actions use independent lock sets."""
    mini = FakeMini()
    registry = create_builtin_registry()
    executor = ActionExecutor(registry=registry, mini=mini)

    head = executor.submit(ActionSpec(name="shake_head", owner_id="main-agent"))
    antenna = executor.submit(
        ActionSpec(
            name="set_antenna",
            params={"right_deg": -45},
            owner_id="worker:w1",
            priority=20,
        )
    )
    head_result, antenna_result = await __import__("asyncio").gather(head, antenna)

    assert head_result.status == "ok"
    assert antenna_result.status == "ok"


@pytest.mark.asyncio
async def test_live2d_action_publishes_native_embodiment_event() -> None:
    """Live2D action emits a native Live2D event instead of robot motion."""
    registry = create_live2d_registry(
        Live2DCapabilities(
            model_name="IceGirl",
            root_url="/static/assets/live2d/IceGirl",
            vtube_file="IceGirl.vtube.json",
            model_file="IceGirl.model3.json",
            idle_motion="DaiJi",
            motions=("DaiJi", "HuiShou"),
            expressions=("惊讶", "脸红"),
        )
    )
    executor = ActionExecutor(registry=registry, mini=object())
    frames: list[object] = []

    async def publish(frame: object) -> None:
        frames.append(frame)

    dispatcher = ActionDispatcher(executor, publish=publish)

    result = await dispatcher.run_action(
        ActionSpec(
            name="live2d_expression_jing_ya",
            owner_id="main-agent",
        )
    )

    assert result.status == "ok"
    assert isinstance(frames[0], EmbodimentFrame)
    assert frames[0].action == "live2d_expression"
    assert frames[0].target == "live2d"
    assert frames[0].payload == {"name": "惊讶"}
    assert isinstance(frames[1], ActionResultFrame)
    assert frames[1].name == "live2d_expression_jing_ya"


@pytest.mark.asyncio
async def test_live2d_registry_rejects_unknown_dynamic_tool() -> None:
    """Unknown Live2D dynamic tool names fail before execution."""
    registry = create_live2d_registry(
        Live2DCapabilities(
            model_name="IceGirl",
            root_url="/static/assets/live2d/IceGirl",
            vtube_file="IceGirl.vtube.json",
            model_file="IceGirl.model3.json",
            idle_motion="DaiJi",
            motions=("DaiJi", "HuiShou"),
            expressions=("惊讶", "脸红"),
        )
    )
    executor = ActionExecutor(registry=registry, mini=object())

    with pytest.raises(UnknownActionError) as exc_info:
        registry.build(ActionSpec(name="live2d_motion_happy", owner_id="main-agent"))

    assert "Unknown action: live2d_motion_happy" in str(exc_info.value)
