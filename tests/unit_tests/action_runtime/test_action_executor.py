"""Tests for v4 action executor lifecycle behavior."""

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


class LifecycleAction(RobotAction):
    """Action that records lifecycle calls."""

    def __init__(self, calls: list[str], *, fail_run: bool = False) -> None:
        """Configure the fake action."""
        super().__init__(
            name="lifecycle",
            required_locks={"head"},
            duration_s=None,
            priority=40,
            interruptible=True,
        )
        self.calls = calls
        self.fail_run = fail_run

    async def prepare(self, context: ActionContext) -> None:
        """Record prepare."""
        self.calls.append("prepare")

    async def run(self, context: ActionContext) -> None:
        """Record run."""
        self.calls.append("run")
        if self.fail_run:
            raise RuntimeError("boom")

    async def cleanup(self, context: ActionContext) -> None:
        """Record cleanup."""
        self.calls.append("cleanup")


def _registry_for(action: RobotAction) -> ActionRegistry:
    registry = ActionRegistry()
    registry.register(
        ActionMetadata(
            name=action.name,
            description="test",
            parameter_schema={"type": "object", "additionalProperties": False},
            required_locks=frozenset(action.required_locks),
            default_priority=action.priority,
            default_interruptible=action.interruptible,
            default_duration_s=action.duration_s,
        ),
        lambda spec: action,
    )
    return registry


@pytest.mark.asyncio
async def test_executor_runs_cleanup_after_success() -> None:
    """cleanup is called after successful runs."""
    calls: list[str] = []
    action = LifecycleAction(calls)
    executor = ActionExecutor(registry=_registry_for(action), mini=object())

    result = await executor.submit(ActionSpec(name="lifecycle", owner_id="manual-test"))

    assert result.status == "ok"
    assert calls == ["prepare", "run", "cleanup"]
    assert executor.lock_manager.active_leases() == {}


@pytest.mark.asyncio
async def test_executor_releases_locks_after_run_error() -> None:
    """Run exceptions are converted to error results and locks are released."""
    calls: list[str] = []
    action = LifecycleAction(calls, fail_run=True)
    executor = ActionExecutor(registry=_registry_for(action), mini=object())

    result = await executor.submit(ActionSpec(name="lifecycle", owner_id="manual-test"))

    assert result.status == "error"
    assert "ActionRunError" in str(result.error)
    assert calls == ["prepare", "run", "cleanup"]
    assert executor.lock_manager.active_leases() == {}


@pytest.mark.asyncio
async def test_executor_cancel_request_returns_cancelled_result() -> None:
    """External cancellation turns a running action into a cancelled result."""
    started = asyncio.Event()

    class WaitingAction(RobotAction):
        def __init__(self) -> None:
            super().__init__(
                name="wait",
                required_locks={"head"},
                duration_s=None,
                priority=40,
                interruptible=True,
            )
            self.cancel_called = False
            self.cleanup_called = False

        async def run(self, context: ActionContext) -> None:
            started.set()
            await context.cancel_token.wait()

        async def cancel(self, context: ActionContext) -> None:
            self.cancel_called = True

        async def cleanup(self, context: ActionContext) -> None:
            self.cleanup_called = True

    action = WaitingAction()
    executor = ActionExecutor(registry=_registry_for(action), mini=object())
    task = asyncio.create_task(
        executor.submit(
            ActionSpec(name="wait", request_id="r1", owner_id="manual-test")
        )
    )

    await started.wait()
    executor.cancel("r1")
    result = await asyncio.wait_for(task, timeout=1.0)

    assert result.status == "cancelled"
    assert action.cancel_called is True
    assert action.cleanup_called is True
    assert executor.lock_manager.active_leases() == {}
