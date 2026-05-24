"""Tests for v4 Brain agent and worker runtime."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from reachy_mini.action_runtime import ActionResult, ActionSpec
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.reachy_brain.agent import BrainAgent, BrainTurnInput
from reachy_mini.reachy_brain.config import AgentConfig, ModelConfig, SpeechConfig, SpeechInputConfig, VisionConfig
from reachy_mini.reachy_brain.coordinator import WorkerCoordinator
from reachy_mini.reachy_brain.memory import WorkerMemory
from reachy_mini.reachy_brain.worker import TaskSpec


def _agent_config() -> AgentConfig:
    return AgentConfig(
        model=ModelConfig(provider="mock", model="mock"),
        speech=SpeechConfig(enabled=False),
        speech_input=SpeechInputConfig(enabled=False),
        vision=VisionConfig(),
        extras={},
    )


@pytest.mark.asyncio
async def test_mock_agent_can_emit_nod_action_spec() -> None:
    """Mock Brain chooses an action but does not execute SDK calls."""
    agent = BrainAgent(config=_agent_config(), registry=create_builtin_registry())

    output = await agent.run(BrainTurnInput(text="你好", turn_id="t1"))

    assert output.reply_text
    assert output.actions[0].name == "nod"
    assert output.actions[0].owner_id == "main-agent"


@pytest.mark.asyncio
async def test_fake_patrol_worker_emits_action_specs_and_memory(tmp_path: Path) -> None:
    """Patrol worker emits bounded-priority action specs and writes JSONL memory."""
    emitted: list[ActionSpec] = []

    async def emit_action(spec: ActionSpec) -> ActionResult:
        emitted.append(spec)
        return ActionResult(
            request_id=spec.request_id,
            action_id="a1",
            name=spec.name,
            owner_id=spec.owner_id,
            status="ok",
        )

    coordinator = WorkerCoordinator(emit_action=emit_action, memory_root=tmp_path)
    spec = TaskSpec(
        task_id="w1",
        task_type="patrol",
        params={"rounds": 2},
        parent_request_id="brain-r1",
        spawned_at=time.monotonic(),
        deadline_s=None,
        memory_namespace="w1",
    )

    await coordinator.spawn(spec)
    result = await coordinator.wait("w1", timeout=1.0)

    assert result.status == "completed"
    assert [item.owner_id for item in emitted] == ["worker:w1", "worker:w1"]
    assert all(item.priority <= 30 for item in emitted)
    assert all(item.parent_request_id == "brain-r1" for item in emitted)
    assert any(event.event == "progress" for event in coordinator.events)
    assert (tmp_path / "w1.jsonl").is_file()
    assert "<status>completed</status>" in coordinator.result_xml["w1"]


@pytest.mark.asyncio
async def test_worker_does_not_block_second_brain_input(tmp_path: Path) -> None:
    """Main agent can answer another input before worker completion."""
    async def emit_action(spec: ActionSpec) -> ActionResult:
        await asyncio.sleep(0.15)
        return ActionResult(
            request_id=spec.request_id,
            action_id="a1",
            name=spec.name,
            owner_id=spec.owner_id,
            status="ok",
        )

    agent = BrainAgent(config=_agent_config(), registry=create_builtin_registry())
    coordinator = WorkerCoordinator(emit_action=emit_action, memory_root=tmp_path)

    first = await agent.run(BrainTurnInput(text="巡视一圈", turn_id="t1"))
    assert first.worker_decision is not None
    await coordinator.handle_decision(
        first.worker_decision,
        parent_request_id=first.actions[0].request_id if first.actions else "brain-r1",
    )
    task_id = str(first.worker_decision["task_id"])
    assert coordinator.is_running(task_id)

    second = await agent.run(BrainTurnInput(text="现在几点", turn_id="t2"))
    assert second.reply_text
    assert coordinator.is_running(task_id)

    await coordinator.wait(task_id, timeout=1.0)


@pytest.mark.asyncio
async def test_worker_update_decision_is_recorded(tmp_path: Path) -> None:
    """WorkerCoordinator accepts update decisions for existing workers."""
    async def emit_action(spec: ActionSpec) -> ActionResult:
        await asyncio.sleep(0.1)
        return ActionResult(
            request_id=spec.request_id,
            action_id="a1",
            name=spec.name,
            owner_id=spec.owner_id,
            status="ok",
        )

    coordinator = WorkerCoordinator(emit_action=emit_action, memory_root=tmp_path)
    task = await coordinator.spawn(
        TaskSpec(
            task_id="w-update",
            task_type="patrol",
            params={"rounds": 1},
            parent_request_id="brain-r1",
            spawned_at=time.monotonic(),
            deadline_s=None,
            memory_namespace="w-update",
        )
    )

    updated_task_id = await coordinator.handle_decision(
        {
            "op": "update",
            "task_id": "w-update",
            "task_type": "patrol",
            "task_params": {"speed": "slow"},
            "summary": "slow down patrol",
        },
        parent_request_id="brain-r1",
    )
    result = await asyncio.wait_for(task, timeout=1.0)

    assert updated_task_id == "w-update"
    assert coordinator.updates["w-update"] == [{"speed": "slow"}]
    assert any(event.payload.get("task_params") == {"speed": "slow"} for event in coordinator.events)
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_worker_cancel_decision_records_cancelled_result(tmp_path: Path) -> None:
    """Cancelled workers finish with a cancelled result and task-result XML."""
    async def emit_action(spec: ActionSpec) -> ActionResult:
        await asyncio.sleep(0.2)
        return ActionResult(
            request_id=spec.request_id,
            action_id="a1",
            name=spec.name,
            owner_id=spec.owner_id,
            status="ok",
        )

    coordinator = WorkerCoordinator(emit_action=emit_action, memory_root=tmp_path)
    await coordinator.spawn(
        TaskSpec(
            task_id="w-cancel",
            task_type="patrol",
            params={"rounds": 2},
            parent_request_id="brain-r1",
            spawned_at=time.monotonic(),
            deadline_s=None,
            memory_namespace="w-cancel",
        )
    )

    cancelled_task_id = await coordinator.handle_decision(
        {
            "op": "cancel",
            "task_id": "w-cancel",
            "task_type": "patrol",
            "task_params": {},
            "summary": "stop patrol",
        },
        parent_request_id="brain-r1",
    )
    result = await coordinator.wait("w-cancel", timeout=1.0)

    assert cancelled_task_id == "w-cancel"
    assert result.status == "cancelled"
    assert any(event.event == "cancelled" for event in coordinator.events)
    assert "<status>cancelled</status>" in coordinator.result_xml["w-cancel"]


def test_worker_memory_read_filter(tmp_path: Path) -> None:
    """Worker memory supports filtered reads for task context injection."""
    memory = WorkerMemory(tmp_path, "demo")
    memory.append({"type": "progress", "value": 1})
    memory.append({"type": "result", "value": 2})

    assert [row["value"] for row in memory.read({"type": "result"})] == [2]
    assert [row["value"] for row in memory.read(lambda row: row["value"] > 1)] == [2]
