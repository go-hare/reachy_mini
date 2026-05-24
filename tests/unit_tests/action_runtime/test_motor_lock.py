"""Tests for v4 motor lock arbitration."""

from __future__ import annotations

import asyncio

import pytest

from reachy_mini.action_runtime import (
    ActionContext,
    ActionExecutor,
    ActionMetadata,
    ActionRegistry,
    ActionSpec,
    RobotAction,
)
from reachy_mini.action_runtime.errors import LockBusyError, LockTimeoutError


class BlockingHeadAction(RobotAction):
    """Head action that runs until cancelled or released."""

    def __init__(self, *, name: str, priority: int, interruptible: bool) -> None:
        """Configure a blocking action."""
        super().__init__(
            name=name,
            required_locks={"head"},
            duration_s=None,
            priority=priority,
            interruptible=interruptible,
        )
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancel_called = False
        self.cleanup_called = False

    async def run(self, context: ActionContext) -> None:
        """Block until release or cancellation."""
        self.started.set()
        release_task = asyncio.create_task(self.release.wait())
        cancel_task = asyncio.create_task(context.cancel_token.wait())
        done, pending = await asyncio.wait(
            {release_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if cancel_task in done:
            await context.cancel_token.checkpoint()

    async def cancel(self, context: ActionContext) -> None:
        """Record cancellation."""
        self.cancel_called = True

    async def cleanup(self, context: ActionContext) -> None:
        """Record cleanup."""
        self.cleanup_called = True


class QuickHeadAction(RobotAction):
    """Head action that finishes immediately."""

    def __init__(self) -> None:
        super().__init__(
            name="quick",
            required_locks={"head"},
            duration_s=0.1,
            priority=40,
            interruptible=True,
        )


def _registry(blocking: BlockingHeadAction) -> ActionRegistry:
    registry = ActionRegistry()
    registry.register(
        ActionMetadata(
            name=blocking.name,
            description="blocking",
            parameter_schema={"type": "object", "additionalProperties": False},
            required_locks=frozenset({"head"}),
            default_priority=blocking.priority,
            default_interruptible=blocking.interruptible,
            default_duration_s=None,
        ),
        lambda spec: blocking,
    )
    quick = QuickHeadAction()
    registry.register(
        ActionMetadata(
            name="quick",
            description="quick",
            parameter_schema={"type": "object", "additionalProperties": False},
            required_locks=frozenset({"head"}),
            default_priority=quick.priority,
            default_interruptible=quick.interruptible,
            default_duration_s=quick.duration_s,
        ),
        lambda spec: quick,
    )
    return registry


@pytest.mark.asyncio
async def test_high_priority_preempts_interruptible_head_lock() -> None:
    """Higher-priority main-agent action preempts a worker head lock."""
    worker_action = BlockingHeadAction(name="worker_head", priority=20, interruptible=True)
    executor = ActionExecutor(registry=_registry(worker_action), mini=object())
    worker = asyncio.create_task(
        executor.submit(
            ActionSpec(
                name="worker_head",
                request_id="r-worker",
                owner_id="worker:w1",
                priority=20,
                interruptible=True,
            )
        )
    )

    await worker_action.started.wait()
    main_result = await executor.submit(
        ActionSpec(
            name="quick",
            request_id="r-main",
            owner_id="main-agent",
            priority=40,
        )
    )
    worker_result = await asyncio.wait_for(worker, timeout=1.0)

    assert main_result.status == "ok"
    assert worker_result.status == "cancelled"
    assert worker_action.cancel_called is True
    assert worker_action.cleanup_called is True
    preempted = next(
        decision
        for decision in executor.lock_manager.decisions
        if decision.decision == "preempted"
    )
    assert preempted.lock_name == "head"
    assert preempted.previous_owner == "worker:w1"
    assert preempted.new_owner == "main-agent"
    assert preempted.request_id == "r-main"
    assert preempted.previous_request_id == "r-worker"
    assert executor.lock_manager.active_leases() == {}


@pytest.mark.asyncio
async def test_uninterruptible_head_lock_rejects_lower_non_safety_action() -> None:
    """Uninterruptible locks reject non-safety preemption."""
    worker_action = BlockingHeadAction(name="worker_head", priority=20, interruptible=False)
    executor = ActionExecutor(registry=_registry(worker_action), mini=object())
    worker = asyncio.create_task(
        executor.submit(
            ActionSpec(
                name="worker_head",
                request_id="r-worker",
                owner_id="worker:w1",
                priority=20,
                interruptible=False,
            )
        )
    )

    await worker_action.started.wait()
    main_result = await executor.submit(
        ActionSpec(
            name="quick",
            request_id="r-main",
            owner_id="main-agent",
            priority=40,
        )
    )
    worker_action.release.set()
    worker_result = await asyncio.wait_for(worker, timeout=1.0)

    assert main_result.status == "error"
    assert "LockBusyError" in str(main_result.error)
    assert worker_result.status == "ok"
    assert worker_action.cancel_called is False
    rejected = next(
        decision
        for decision in executor.lock_manager.decisions
        if decision.decision == "rejected"
    )
    assert rejected.lock_name == "head"
    assert rejected.previous_owner == "worker:w1"
    assert rejected.new_owner == "main-agent"
    assert rejected.request_id == "r-main"


@pytest.mark.asyncio
async def test_head_lock_conflicts_with_head_yaw_child_lock() -> None:
    """Coarse head lock conflicts with child head-axis locks."""
    from reachy_mini.action_runtime import CancelToken
    from reachy_mini.action_runtime.motor_lock import MotorLockManager

    manager = MotorLockManager()
    await manager.acquire(
        required_locks={"head_yaw"},
        owner_id="manual-test",
        action_id="a1",
        request_id="r1",
        priority=40,
        interruptible=False,
        cancel_token=CancelToken(),
    )

    with pytest.raises(LockBusyError):
        await manager.acquire(
            required_locks={"head"},
            owner_id="main-agent",
            action_id="a2",
            request_id="r2",
            priority=40,
            interruptible=True,
            cancel_token=CancelToken(),
        )


@pytest.mark.asyncio
async def test_waiting_for_busy_lock_times_out_with_timeout_error() -> None:
    """A positive wait timeout reports timeout instead of an immediate conflict."""
    from reachy_mini.action_runtime import CancelToken
    from reachy_mini.action_runtime.motor_lock import MotorLockManager

    manager = MotorLockManager()
    await manager.acquire(
        required_locks={"head"},
        owner_id="worker:w1",
        action_id="a1",
        request_id="r1",
        priority=40,
        interruptible=False,
        cancel_token=CancelToken(),
    )

    with pytest.raises(LockTimeoutError):
        await manager.acquire(
            required_locks={"head"},
            owner_id="main-agent",
            action_id="a2",
            request_id="r2",
            priority=40,
            interruptible=True,
            cancel_token=CancelToken(),
            wait_timeout_s=0.01,
        )
