"""Action executor that binds action specs to SDK-side robot actions."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .action import (
    ActionResult,
    ActionSpec,
    CancelToken,
    Clock,
    ExecutorActionContext,
    SystemClock,
    make_action_id,
)
from .errors import ActionCancelledError, ActionRunError, ActionRuntimeError
from .motor_lock import MotorLockManager
from .registry import ActionRegistry


class ActionExecutor:
    """Validate, lock, run, cancel, and clean up robot actions."""

    def __init__(
        self,
        *,
        registry: ActionRegistry,
        mini: object,
        lock_manager: MotorLockManager | None = None,
        clock: Clock | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create an executor for one robot SDK object."""
        self.registry = registry
        self.mini = mini
        self.lock_manager = lock_manager or MotorLockManager()
        self.clock = clock or SystemClock()
        self.logger = logger or logging.getLogger(__name__)
        self._running: dict[str, CancelToken] = {}

    async def submit(self, spec: ActionSpec, *, wait_timeout_s: float = 0.0) -> ActionResult:
        """Run one action spec to completion and return an action result."""
        if not spec.owner_id:
            raise ValueError("ActionSpec.owner_id is required.")

        action = self.registry.build(spec)
        request_id = spec.request_id or make_action_id("request")
        action_id = make_action_id(action.name)
        cancel_token = CancelToken()
        priority = max(spec.priority, action.priority)
        interruptible = bool(spec.interruptible and action.interruptible)
        run_timeout_s = self._resolve_run_timeout(spec, action.duration_s)
        lease_ttl_s = self._resolve_lease_ttl(spec, action.duration_s)
        start = self.clock.monotonic()
        leases: dict[str, object] = {}
        context = ExecutorActionContext(
            mini=self.mini,
            cancel_token=cancel_token,
            logger=self.logger,
            request_id=request_id,
            owner_id=spec.owner_id,
            lease_handles=leases,
            clock=self.clock,
        )
        self._running[request_id] = cancel_token

        try:
            leases.update(
                await self.lock_manager.acquire(
                    required_locks=action.required_locks,
                    owner_id=spec.owner_id,
                    action_id=action_id,
                    request_id=request_id,
                    priority=priority,
                    interruptible=interruptible,
                    cancel_token=cancel_token,
                    ttl_s=lease_ttl_s,
                    wait_timeout_s=wait_timeout_s,
                )
            )
            await action.prepare(context)
            action_output = await self._run_with_cancellation(action, context, run_timeout_s)
            return ActionResult(
                request_id=request_id,
                action_id=action_id,
                name=action.name,
                owner_id=spec.owner_id,
                status="ok",
                result=action_output,
                duration_ms=self._elapsed_ms(start),
                reason=spec.reason,
            )
        except ActionCancelledError as exc:
            await self._cancel_action(action, context)
            return ActionResult(
                request_id=request_id,
                action_id=action_id,
                name=action.name,
                owner_id=spec.owner_id,
                status="cancelled",
                error=str(exc),
                duration_ms=self._elapsed_ms(start),
                reason=spec.reason,
            )
        except Exception as exc:
            if isinstance(exc, ActionRuntimeError):
                error = exc
            else:
                error = ActionRunError(f"Action {action.name} failed: {exc}")
                error.__cause__ = exc
            return ActionResult(
                request_id=request_id,
                action_id=action_id,
                name=action.name,
                owner_id=spec.owner_id,
                status="error",
                error=f"{type(error).__name__}: {error}",
                duration_ms=self._elapsed_ms(start),
                reason=spec.reason,
            )
        finally:
            try:
                await action.cleanup(context)
            except Exception:
                self.logger.exception("Action cleanup failed for %s", action.name)
            await self.lock_manager.release_all(context.lease_handles)
            self._running.pop(request_id, None)

    def cancel(self, request_id: str | None = None) -> None:
        """Cancel one request or every running request."""
        if request_id is None:
            for token in self._running.values():
                token.cancel()
            return
        token = self._running.get(request_id)
        if token is not None:
            token.cancel()

    async def _run_with_cancellation(
        self,
        action: object,
        context: ExecutorActionContext,
        duration_s: float | None,
    ) -> object:
        run_coro = action.run(context)  # type: ignore[attr-defined]
        cancel_task = asyncio.create_task(context.cancel_token.wait())
        run_task = asyncio.create_task(run_coro)
        tasks = {cancel_task, run_task}
        try:
            done, _ = await asyncio.wait(
                tasks,
                timeout=duration_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                context.cancel_token.cancel()
                raise ActionCancelledError("Action deadline exceeded.")
            if cancel_task in done and context.cancel_token.is_cancelled:
                run_task.cancel()
                raise ActionCancelledError("Action was cancelled.")
            result = await run_task
            await context.cancel_token.checkpoint()
            return result
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    async def _cancel_action(
        self,
        action: object,
        context: ExecutorActionContext,
    ) -> None:
        try:
            await action.cancel(context)  # type: ignore[attr-defined]
        except Exception:
            self.logger.exception("Action cancel failed for %s", getattr(action, "name", "unknown"))

    def _resolve_run_timeout(
        self,
        spec: ActionSpec,
        action_duration_s: float | None,
    ) -> float | None:
        if spec.deadline_s is not None:
            return spec.deadline_s
        if action_duration_s is None:
            return None
        return action_duration_s + 1.0

    def _resolve_lease_ttl(
        self,
        spec: ActionSpec,
        action_duration_s: float | None,
    ) -> float | None:
        if spec.deadline_s is not None:
            return spec.deadline_s + 1.0
        if action_duration_s is None:
            return None
        return action_duration_s + 1.0

    def _elapsed_ms(self, start: float) -> int:
        return int((self.clock.monotonic() - start) * 1000)
