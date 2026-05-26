"""Test helpers for SDK-first v4 tests."""

from __future__ import annotations

import uuid
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
)

from reachy_mini.action_runtime import ActionResult, ActionSpec
from reachy_mini.reachy_brain.agent import BrainTurnInput
from reachy_mini.reachy_brain.config import (
    AgentConfig,
    ModelConfig,
    SpeechConfig,
    SpeechInputConfig,
    VisionConfig,
)
from reachy_mini.reachy_brain.offline_sdk_client import OfflineSDKClient


def agent_config(*, speech_enabled: bool = False) -> AgentConfig:
    """Return a minimal AgentConfig for SDK-first unit tests."""
    return AgentConfig(
        model=ModelConfig(provider="mock", model="mock"),
        speech=SpeechConfig(enabled=speech_enabled),
        speech_input=SpeechInputConfig(enabled=False),
        vision=VisionConfig(),
        extras={},
    )


class FakeMini:
    """Small fake SDK object for action runtime tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.antennas = [0.0, 0.0]

    def goto_target(self, **kwargs: Any) -> None:
        self.calls.append(("goto_target", kwargs))

    def look_at_world(self, x, y, z, duration, perform_movement) -> None:
        self.calls.append(("look_at_world", {"x": x, "y": y, "z": z}))

    def get_present_antenna_joint_positions(self) -> list[float]:
        return list(self.antennas)


class ScriptedSDKClient(OfflineSDKClient):
    """Explicit fake Claude SDK client yielding scripted SDK messages."""

    def __init__(
        self,
        options: Any = None,
        *,
        scripts: list[list[Any]] | None = None,
        action_runner=None,
    ) -> None:
        super().__init__(options, action_runner=action_runner)
        self.scripts = list(scripts or [])
        self.turn_inputs: list[BrainTurnInput] = []

    async def query(self, prompt: str, session_id: str = "default") -> None:
        await super().query(prompt, session_id=session_id)
        self.turn_inputs.append(
            BrainTurnInput(
                text=prompt.split("\n", 1)[0],
                turn_id=session_id,
                context={"formatted_prompt": prompt},
            )
        )

    async def receive_response(self):
        if self.scripts:
            for message in self.scripts.pop(0):
                yield message
            return
        async for message in super().receive_response():
            yield message


def assistant_message(text: str, *, session_id: str = "S") -> AssistantMessage:
    """Build an SDK AssistantMessage with one TextBlock."""
    return AssistantMessage(
        content=[TextBlock(text=text)],
        model="test-model",
        session_id=session_id,
    )


def result_message(*, session_id: str = "S") -> ResultMessage:
    """Build an SDK ResultMessage ending a turn."""
    return ResultMessage(
        subtype="success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id=session_id,
    )


def task_started(task_id: str, *, session_id: str = "S") -> TaskStartedMessage:
    """Build an SDK task-started message."""
    return TaskStartedMessage(
        subtype="task_started",
        data={"source": "test"},
        task_id=task_id,
        description="test task",
        uuid=str(uuid.uuid4()),
        session_id=session_id,
    )


def task_progress(task_id: str, *, session_id: str = "S") -> TaskProgressMessage:
    """Build an SDK task-progress message."""
    return TaskProgressMessage(
        subtype="task_progress",
        data={"source": "test"},
        task_id=task_id,
        description="test task",
        usage={"total_tokens": 0, "tool_uses": 0, "duration_ms": 0},
        uuid=str(uuid.uuid4()),
        session_id=session_id,
    )


def task_completed(task_id: str, *, session_id: str = "S") -> TaskNotificationMessage:
    """Build an SDK task-completed message."""
    return TaskNotificationMessage(
        subtype="task_notification",
        data={"source": "test"},
        task_id=task_id,
        status="completed",
        output_file="",
        summary="done",
        uuid=str(uuid.uuid4()),
        session_id=session_id,
    )


async def run_action_ok(spec: ActionSpec) -> ActionResult:
    """Return an ok ActionResult without touching hardware."""
    return ActionResult(
        request_id=spec.request_id,
        action_id=f"fake_{spec.name}",
        name=spec.name,
        owner_id=spec.owner_id,
        status="ok",
        duration_ms=0,
    )
