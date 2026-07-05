"""RobotRuntime System 1 facade."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace

from reachy_mini.robot_runtime.adapters.base import RobotAdapter
from reachy_mini.robot_runtime.behavior_tree import (
    RobotBehaviorTree,
    build_robot_tree,
)
from reachy_mini.robot_runtime.blackboard import RobotBlackboard
from reachy_mini.robot_runtime.bridges.policy_service import PolicyServiceBackend
from reachy_mini.robot_runtime.capabilities import CapabilityRegistry
from reachy_mini.robot_runtime.config import RobotRuntimeConfig
from reachy_mini.robot_runtime.contracts import (
    AdapterResult,
    AdapterResultStatus,
    AdapterState,
    BehaviorPlan,
    EmbodiedIntent,
    LifecycleState,
    RobotCommand,
    RobotEvent,
    SafetyDecision,
    SafetyState,
    SafetyStatus,
    TimingAnchor,
    now_ms,
)
from reachy_mini.robot_runtime.lifecycle import LifecycleManager
from reachy_mini.robot_runtime.metrics import RobotMetricsSnapshot
from reachy_mini.robot_runtime.mirrors import (
    publish_event_mirrors,
    publish_state_mirrors,
)
from reachy_mini.robot_runtime.policy import (
    RobotPolicyEngine,
    behavior_node_for_policy,
    create_default_policy_engine,
)
from reachy_mini.robot_runtime.resolver import CapabilityResolver
from reachy_mini.robot_runtime.safety import SafetySupervisor
from reachy_mini.robot_runtime.scheduler import ScheduledPlan, TimelineScheduler
from reachy_mini.robot_runtime.state import RobotStateSnapshot, RobotStateStore
from reachy_mini.robot_runtime.telemetry import RobotTelemetrySink, RobotTraceTimeline
from reachy_mini.robot_runtime.visualization import (
    RerunExportResult,
    RobotVisualizationRecord,
    export_timeline_to_rerun,
    visualization_records,
)

LOGGER = logging.getLogger(__name__)

PublishEvent = Callable[[RobotEvent], Awaitable[None]]
_AUTO_EXECUTE_ANCHORS = frozenset(
    {
        TimingAnchor.IDLE.value,
        TimingAnchor.INTERRUPT.value,
        TimingAnchor.TURN_START.value,
    }
)


@dataclass(slots=True)
class _ActiveCommand:
    command: RobotCommand
    adapter: RobotAdapter
    task: asyncio.Task[AdapterResult]
    cancel_reason: str = "command_cancelled"


@dataclass(slots=True)
class RobotRuntimeDependencies:
    """Dependencies injected into RobotRuntime."""

    publish_event: PublishEvent | None = None
    adapters: dict[str, RobotAdapter] = field(default_factory=dict)
    policy_backend: PolicyServiceBackend | None = None


@dataclass(slots=True)
class RobotRuntime:
    """Enterprise RobotRuntime facade that owns System 1 state and ticks."""

    config: RobotRuntimeConfig = field(default_factory=RobotRuntimeConfig)
    dependencies: RobotRuntimeDependencies = field(default_factory=RobotRuntimeDependencies)
    lifecycle: LifecycleManager = field(default_factory=LifecycleManager)
    state_store: RobotStateStore = field(default_factory=RobotStateStore)
    blackboard: RobotBlackboard = field(default_factory=RobotBlackboard.create)
    capability_registry: CapabilityRegistry = field(default_factory=CapabilityRegistry)
    scheduler: TimelineScheduler = field(default_factory=TimelineScheduler)
    safety: SafetySupervisor = field(default_factory=SafetySupervisor)
    telemetry: RobotTelemetrySink = field(default_factory=RobotTelemetrySink)
    policy_engine: RobotPolicyEngine | None = None
    resolver: CapabilityResolver | None = None
    behavior_tree: RobotBehaviorTree | None = None
    _tick_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _active_commands: dict[str, _ActiveCommand] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _intent_index: dict[str, EmbodiedIntent] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _execution_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    _stopping: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Create behavior tree after blackboard construction."""
        if self.behavior_tree is None:
            self.behavior_tree = build_robot_tree(self.blackboard)
        if self.policy_engine is None:
            self.policy_engine = create_default_policy_engine(
                policy_backend=self.dependencies.policy_backend
            )
        if self.resolver is None:
            self.resolver = CapabilityResolver(self.capability_registry)
        self._apply_safety_profile()
        self.state_store.set_mode(self.config.mode)
        self.state_store.set_capability_revision(self.capability_registry.revision)
        self.blackboard.update_robot_state(self.state_store.snapshot().state)

    async def configure(self) -> None:
        """Configure runtime components and enter inactive state."""
        if self.lifecycle.state == LifecycleState.UNCONFIGURED:
            transition = self.lifecycle.configure(reason="runtime configure")
            await self._publish_lifecycle_event(transition.target.value)
            transition = self.lifecycle.mark_inactive(reason="runtime configured")
            self.state_store.set_lifecycle(transition.target)
            self.blackboard.update_robot_state(self.state_store.snapshot().state)
            await self._publish_state_snapshot()
            await self._publish_lifecycle_event(transition.target.value)
            await self._activate_registered_adapters()

    async def activate(self) -> None:
        """Activate the runtime so the behavior tree can progress."""
        if self.lifecycle.state == LifecycleState.UNCONFIGURED:
            await self.configure()
        if self.lifecycle.state == LifecycleState.INACTIVE:
            transition = self.lifecycle.activate(reason="runtime activate")
            await self._publish_lifecycle_event(transition.target.value)
            transition = self.lifecycle.mark_active(reason="runtime active")
            self.state_store.set_lifecycle(transition.target)
            self.blackboard.update_robot_state(self.state_store.snapshot().state)
            await self._publish_state_snapshot()
            await self._publish_lifecycle_event(transition.target.value)

    async def start(self) -> None:
        """Start the non-blocking behavior tree tick loop."""
        if self._tick_task is not None and not self._tick_task.done():
            return
        await self.activate()
        self._stopping = False
        self._tick_task = asyncio.create_task(self._tick_loop(), name="robot-runtime-tick")

    async def stop(self) -> None:
        """Stop the tick loop and deactivate runtime state."""
        self._stopping = True
        if self._tick_task is not None:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
            self._tick_task = None
        await self._cancel_active_commands(reason="runtime_stop")
        await self._cancel_scheduled_plans(reason="runtime_stop")
        if self.lifecycle.state == LifecycleState.ACTIVE:
            await self._deactivate_registered_adapters()
            transition = self.lifecycle.deactivate(reason="runtime stop")
            await self._publish_lifecycle_event(transition.target.value)
            transition = self.lifecycle.mark_inactive(reason="runtime stopped")
            self.state_store.set_lifecycle(transition.target)
            self.blackboard.update_robot_state(self.state_store.snapshot().state)
            await self._publish_state_snapshot()
            await self._publish_lifecycle_event(transition.target.value)

    async def accept_intent(self, intent: EmbodiedIntent) -> RobotEvent:
        """Accept an embodied intent and enqueue it for the behavior tree."""
        self._intent_index[intent.intent_id] = intent
        self.blackboard.enqueue_intent(intent)
        event = RobotEvent(
            source="runtime",
            event_type="intent_received",
            turn_id=intent.turn_id or None,
            intent_id=intent.intent_id,
            payload={"intent_type": intent.intent_type.value, "priority": intent.priority},
        )
        await self._publish(event)
        return event

    async def execute_ready(
        self,
        *,
        anchor: TimingAnchor | str | None = None,
        now_ms_value: int | None = None,
        occupied_channels: set[str] | None = None,
    ) -> list[RobotEvent]:
        """Execute currently due plans from the scheduler."""
        async with self._execution_lock:
            items = self.scheduler.pop_ready(
                anchor=anchor,
                now_ms_value=now_ms_value,
                occupied_channels=occupied_channels,
                allowed_anchors=None if anchor is not None else _AUTO_EXECUTE_ANCHORS,
            )
            events: list[RobotEvent] = []
            for item in items:
                events.extend(await self._execute_scheduled_plan(item))
            return events

    async def register_adapter(
        self,
        adapter: RobotAdapter,
        *,
        config: dict[str, object] | None = None,
    ) -> None:
        """Register one adapter and ingest its capability inventory."""
        await adapter.configure(dict(config or {}))
        self.dependencies.adapters[adapter.adapter_id] = adapter
        self.capability_registry.register_many(await adapter.capabilities())
        self.state_store.set_capability_revision(self.capability_registry.revision)
        await self._refresh_adapter_state(adapter)
        await self._publish(
            RobotEvent(
                source="runtime",
                event_type="adapter_registered",
                status=adapter.adapter_id,
                payload={"capability_revision": self.capability_registry.revision},
            )
        )
        if self.lifecycle.state is LifecycleState.ACTIVE:
            await self._activate_adapter(adapter)

    async def handle_intent(self, intent: EmbodiedIntent) -> list[RobotEvent]:
        """Accept, plan, resolve, safety-check, and execute one intent."""
        events: list[RobotEvent] = [await self.accept_intent(intent)]
        if self.policy_engine is None:
            raise RuntimeError("RobotRuntime policy_engine is not configured")
        policy_result = self.policy_engine.plan_intent(
            intent,
            robot_state=self.state_store.snapshot().state,
        )
        plan = policy_result.plan
        behavior_node = _behavior_node_from_plan(
            plan,
            policy_name=policy_result.policy_name,
        )
        try:
            schedule_decision = self.scheduler.schedule_with_decision(plan)
        except ValueError as exc:
            rejected_event = RobotEvent(
                source="scheduler",
                event_type="plan_rejected",
                severity="warning",
                turn_id=intent.turn_id or None,
                intent_id=intent.intent_id,
                plan_id=plan.plan_id,
                status="failed",
                reason=str(exc),
                payload={
                    "policy_name": policy_result.policy_name,
                    "behavior_node": behavior_node,
                    "policy_reasons": list(policy_result.reasons),
                    "rejection_reason": str(exc),
                    "timing_anchor": plan.timing_anchor.value,
                    "start_after_ms": plan.start_after_ms,
                    "deadline_ms": plan.deadline_ms,
                },
            )
            await self._publish(rejected_event)
            events.append(rejected_event)
            return events
        for interrupted in schedule_decision.interrupted:
            interrupted_event = RobotEvent(
                source="scheduler",
                event_type="plan_interrupted",
                turn_id=intent.turn_id or None,
                intent_id=interrupted.plan.intent_id,
                plan_id=interrupted.plan.plan_id,
                status=interrupted.plan.status.value,
                reason=interrupted.reason,
                payload={"interrupted_by_plan_id": plan.plan_id},
            )
            await self._publish(interrupted_event)
            events.append(interrupted_event)
        scheduled = schedule_decision.scheduled
        scheduled_event = RobotEvent(
            source="scheduler",
            event_type="plan_scheduled",
            turn_id=intent.turn_id or None,
            intent_id=intent.intent_id,
            plan_id=plan.plan_id,
            status=scheduled.plan.status.value,
            payload={
                "ready_at_ms": scheduled.ready_at_ms,
                "policy_name": policy_result.policy_name,
                "behavior_node": behavior_node,
                "policy_reasons": list(policy_result.reasons),
            },
        )
        await self._publish(scheduled_event)
        events.append(scheduled_event)

        events.extend(await self.execute_ready(now_ms_value=now_ms()))
        return events

    async def record_tool_error(
        self,
        *,
        tool_name: str,
        exc: Exception,
        turn_id: str = "",
        payload: dict[str, object] | None = None,
    ) -> RobotEvent:
        """Record a model-facing Robot Intent API validation failure."""
        message = str(exc).strip() or type(exc).__name__
        event = RobotEvent(
            source="tool",
            event_type="tool_rejected",
            severity="warning",
            turn_id=turn_id or None,
            status="failed",
            reason=f"{type(exc).__name__}: {message}",
            payload={
                "tool": tool_name,
                "error_type": type(exc).__name__,
                **dict(payload or {}),
            },
        )
        await self._publish(event)
        return event

    def snapshot(self) -> RobotStateSnapshot:
        """Return the latest RobotState snapshot."""
        return self.state_store.snapshot()

    def trace_timeline(
        self,
        *,
        turn_id: str | None = None,
        intent_id: str | None = None,
        plan_id: str | None = None,
        command_id: str | None = None,
    ) -> RobotTraceTimeline:
        """Return an observable timeline for one turn, intent, plan, or command."""
        return self.telemetry.trace_timeline(
            turn_id=turn_id,
            intent_id=intent_id,
            plan_id=plan_id,
            command_id=command_id,
        )

    def metrics(self) -> RobotMetricsSnapshot:
        """Return current derived Runtime metrics."""
        return self.telemetry.metrics()

    def behavior_tree_snapshot(self) -> dict[str, object]:
        """Return the latest behavior tree node status snapshot."""
        if self.behavior_tree is None:
            raise RuntimeError("RobotRuntime behavior_tree is not configured")
        return self.behavior_tree.snapshot()

    def structured_log_lines(self, *, limit: int | None = None) -> list[str]:
        """Return RobotRuntime telemetry as structured JSON lines."""
        return self.telemetry.structured_log_lines(limit=limit)

    def visualization_records(
        self,
        *,
        turn_id: str | None = None,
        intent_id: str | None = None,
        plan_id: str | None = None,
        command_id: str | None = None,
        entity_prefix: str = "robot_runtime",
    ) -> list[RobotVisualizationRecord]:
        """Return Rerun-compatible visualization records for one trace."""
        timeline = self.trace_timeline(
            turn_id=turn_id,
            intent_id=intent_id,
            plan_id=plan_id,
            command_id=command_id,
        )
        return visualization_records(timeline, entity_prefix=entity_prefix)

    def export_trace_to_rerun(
        self,
        *,
        turn_id: str | None = None,
        intent_id: str | None = None,
        plan_id: str | None = None,
        command_id: str | None = None,
        recorder: object | None = None,
        entity_prefix: str = "robot_runtime",
    ) -> RerunExportResult:
        """Export one trace timeline to a Rerun-compatible recorder."""
        timeline = self.trace_timeline(
            turn_id=turn_id,
            intent_id=intent_id,
            plan_id=plan_id,
            command_id=command_id,
        )
        return export_timeline_to_rerun(
            timeline,
            recorder=recorder,
            entity_prefix=entity_prefix,
        )

    async def _execute_adapter_command(
        self,
        adapter: RobotAdapter,
        command: RobotCommand,
    ) -> AdapterResult:
        task = asyncio.create_task(
            adapter.execute(command),
            name=f"robot-command-{command.command_id}",
        )
        await self._track_active_command(command, adapter, task)
        try:
            return await asyncio.wait_for(
                task,
                timeout=_timeout_s(command.timeout_ms),
            )
        except TimeoutError:
            with contextlib.suppress(Exception):
                await adapter.cancel(command.command_id, "command_timeout")
            ended_at_ms = now_ms()
            return AdapterResult(
                command_id=command.command_id,
                adapter_id=command.adapter_id,
                status=AdapterResultStatus.TIMEOUT,
                ended_at_ms=ended_at_ms,
                error_code="command_timeout",
                error_message=f"command timed out after {command.timeout_ms}ms",
                telemetry={"timeout_ms": command.timeout_ms},
            )
        except asyncio.CancelledError:
            active = self._active_commands.get(command.command_id)
            cancel_reason = active.cancel_reason if active else "command_cancelled"
            with contextlib.suppress(Exception):
                await adapter.cancel(command.command_id, cancel_reason)
            return AdapterResult(
                command_id=command.command_id,
                adapter_id=command.adapter_id,
                status=AdapterResultStatus.CANCELLED,
                error_code="command_cancelled",
                error_message=cancel_reason,
            )
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            return AdapterResult(
                command_id=command.command_id,
                adapter_id=command.adapter_id,
                status=AdapterResultStatus.FAILED,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
        finally:
            await self._clear_active_command(command.command_id)

    async def cancel_task(self, task_id: str, *, reason: str = "") -> RobotEvent:
        """Cancel a scheduled plan or active command by plan id or command id."""
        cancel_reason = reason or "task_cancelled"
        cancelled_plans = []
        direct_plan = self.scheduler.cancel(task_id, reason=cancel_reason)
        if direct_plan is not None:
            cancelled_plans.append(direct_plan)
        for scheduled in self.scheduler.list_scheduled():
            if _matches_scheduled_task_id(scheduled.plan, task_id):
                cancelled = self.scheduler.cancel(
                    scheduled.plan.plan_id,
                    reason=cancel_reason,
                )
                if cancelled is not None:
                    cancelled_plans.append(cancelled)
        active = [
            record
            for record in self._active_commands.values()
            if _matches_task_id(record.command, task_id)
        ]
        for record in active:
            record.cancel_reason = cancel_reason
            record.task.cancel()
        event = RobotEvent(
            source="runtime",
            event_type="task_cancel_requested",
            status="accepted" if cancelled_plans or active else "not_found",
            payload={
                "task_id": task_id,
                "cancelled_plan_id": cancelled_plans[0].plan.plan_id
                if cancelled_plans
                else None,
                "cancelled_plan_ids": [item.plan.plan_id for item in cancelled_plans],
                "active_command_ids": [item.command.command_id for item in active],
            },
            reason=reason or None,
        )
        await self._publish(event)
        return event

    async def _cancel_scheduled_plans(self, *, reason: str) -> None:
        scheduled = self.scheduler.list_scheduled()
        if not scheduled:
            return
        cancelled = [
            self.scheduler.cancel(item.plan.plan_id, reason=reason)
            for item in scheduled
        ]
        await self._publish(
            RobotEvent(
                source="runtime",
                event_type="scheduled_plans_cancel_requested",
                status="accepted",
                payload={
                    "reason": reason,
                    "plan_ids": [
                        item.plan.plan_id for item in cancelled if item is not None
                    ],
                },
                reason=reason,
            )
        )

    async def _cancel_active_commands(self, *, reason: str) -> None:
        active = list(self._active_commands.values())
        if not active:
            return
        for record in active:
            record.cancel_reason = reason
            record.task.cancel()
        await self._publish(
            RobotEvent(
                source="runtime",
                event_type="active_commands_cancel_requested",
                status="accepted",
                payload={
                    "reason": reason,
                    "active_command_ids": [
                        record.command.command_id for record in active
                    ],
                },
                reason=reason,
            )
        )
        active_ids = {record.command.command_id for record in active}
        for _ in range(100):
            if not active_ids & set(self._active_commands):
                return
            await asyncio.sleep(0.001)
        remaining = sorted(active_ids & set(self._active_commands))
        await self._publish(
            RobotEvent(
                source="runtime",
                event_type="active_commands_cancel_timeout",
                severity="warning",
                status="timeout",
                payload={"active_command_ids": remaining},
                reason=reason,
            )
        )

    async def _tick_loop(self) -> None:
        interval_s = 1.0 / max(int(self.config.tick_hz), 1)
        while not self._stopping:
            try:
                self.tick_once()
                await self.execute_ready(now_ms_value=now_ms())
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                LOGGER.exception("RobotRuntime tick failed: %s", exc)
                await self._publish(
                    RobotEvent(
                        source="runtime",
                        event_type="tick_failed",
                        severity="error",
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
            await asyncio.sleep(interval_s)

    def tick_once(self) -> str:
        """Tick the behavior tree once and return the root status value."""
        if self.behavior_tree is None:
            raise RuntimeError("RobotRuntime behavior_tree is not configured")
        self.blackboard.update_robot_state(self.state_store.snapshot().state)
        status = self.behavior_tree.tick()
        return status.value

    async def _execute_scheduled_plan(self, item: ScheduledPlan) -> list[RobotEvent]:
        events: list[RobotEvent] = []
        plan = item.plan
        intent = self._intent_index.get(plan.intent_id)
        turn_id = intent.turn_id if intent is not None and intent.turn_id else None
        behavior_node = _behavior_node_from_plan(plan)
        for resolution in self.resolver.resolve_plan(plan) if self.resolver else []:
            if resolution.command is None or resolution.match is None:
                event = RobotEvent(
                    source="resolver",
                    event_type="capability_unresolved",
                    severity="warning",
                    turn_id=turn_id,
                    intent_id=plan.intent_id,
                    plan_id=plan.plan_id,
                    status=resolution.status,
                    reason=";".join(resolution.reasons),
                    payload={
                        "behavior_node": behavior_node,
                        "reasons": list(resolution.reasons),
                        "step_id": resolution.step_id,
                    },
                )
                await self._publish(event)
                events.append(event)
                continue
            command = resolution.command
            capability = resolution.match.capability
            resolved_event = RobotEvent(
                source="resolver",
                event_type="command_resolved",
                turn_id=turn_id,
                intent_id=plan.intent_id,
                plan_id=command.plan_id,
                command_id=command.command_id,
                status=capability.capability_id,
                payload={
                    "adapter_id": command.adapter_id,
                    "behavior_node": behavior_node,
                    "selected_capability": capability.capability_id,
                },
            )
            await self._publish(resolved_event)
            events.append(resolved_event)

            decision = self.safety.evaluate(
                command,
                self.state_store.snapshot().state,
                capability,
            )
            safety_event = RobotEvent(
                source="safety",
                event_type="safety_decision",
                severity="warning"
                if decision.status
                in {SafetyStatus.DENY, SafetyStatus.DEGRADE, SafetyStatus.DELAY}
                else "info",
                turn_id=turn_id,
                intent_id=plan.intent_id,
                plan_id=command.plan_id,
                command_id=command.command_id,
                status=decision.status.value,
                payload={
                    **decision.to_dict(),
                    "adapter_id": command.adapter_id,
                    "behavior_node": behavior_node,
                    "selected_capability": capability.capability_id,
                },
                reason=";".join(decision.reasons) or None,
            )
            await self._publish(safety_event)
            events.append(safety_event)
            if decision.status in {SafetyStatus.DENY, SafetyStatus.DELAY}:
                continue
            effective_command = _command_with_safety_envelope(
                decision.replacement_command or command,
                decision,
            )
            adapter = self.dependencies.adapters.get(effective_command.adapter_id)
            if adapter is None:
                event = RobotEvent(
                    source="adapter",
                    event_type="adapter_missing",
                    severity="error",
                    turn_id=turn_id,
                    intent_id=plan.intent_id,
                    plan_id=effective_command.plan_id,
                    command_id=effective_command.command_id,
                    status=effective_command.adapter_id,
                    payload={
                        "adapter_id": effective_command.adapter_id,
                        "behavior_node": behavior_node,
                        "capability_id": effective_command.capability_id,
                        "selected_capability": effective_command.capability_id,
                    },
                )
                await self._publish(event)
                events.append(event)
                continue
            started_event = RobotEvent(
                source="runtime",
                event_type="command_started",
                turn_id=turn_id,
                intent_id=plan.intent_id,
                plan_id=effective_command.plan_id,
                command_id=effective_command.command_id,
                status="running",
                payload={
                    "adapter_id": effective_command.adapter_id,
                    "behavior_node": behavior_node,
                    "capability_id": effective_command.capability_id,
                    "command_type": effective_command.command_type,
                    "selected_capability": effective_command.capability_id,
                    "safety_decision_id": decision.decision_id,
                },
            )
            await self._publish(started_event)
            events.append(started_event)
            result = await self._execute_adapter_command(adapter, effective_command)
            with contextlib.suppress(Exception):
                self.state_store.set_adapter_state(await adapter.state())
                await self._publish_state_snapshot()
            result_event = _event_from_adapter_result(
                result,
                intent_id=plan.intent_id,
                turn_id=turn_id,
                plan_id=effective_command.plan_id,
                behavior_node=behavior_node,
                selected_capability=effective_command.capability_id,
            )
            await self._publish(result_event)
            events.append(result_event)
        return events

    async def _publish_lifecycle_event(self, state: str) -> None:
        await self._publish(
            RobotEvent(
                source="lifecycle",
                event_type="lifecycle_transition",
                status=state,
            )
        )

    async def _publish(self, event: RobotEvent) -> None:
        self.telemetry.record(event)
        if self.dependencies.publish_event is not None:
            await self.dependencies.publish_event(event)
        await publish_event_mirrors(self.dependencies.adapters, event)

    async def _publish_state_snapshot(self) -> None:
        await publish_state_mirrors(
            self.dependencies.adapters,
            self.state_store.snapshot().state,
        )

    async def _activate_registered_adapters(self) -> None:
        for adapter in list(self.dependencies.adapters.values()):
            await self._activate_adapter(adapter)

    async def _deactivate_registered_adapters(self) -> None:
        for adapter in list(self.dependencies.adapters.values()):
            await self._deactivate_adapter(adapter)

    async def _activate_adapter(self, adapter: RobotAdapter) -> None:
        try:
            await adapter.activate()
            await self._refresh_adapter_state(adapter)
        except Exception as exc:
            await self._record_adapter_lifecycle_error(
                adapter.adapter_id,
                event_type="adapter_activate_failed",
                exc=exc,
            )
            return
        await self._publish(
            RobotEvent(
                source="runtime",
                event_type="adapter_activated",
                status=adapter.adapter_id,
            )
        )

    async def _deactivate_adapter(self, adapter: RobotAdapter) -> None:
        try:
            await adapter.deactivate()
            await self._refresh_adapter_state(adapter)
        except Exception as exc:
            await self._record_adapter_lifecycle_error(
                adapter.adapter_id,
                event_type="adapter_deactivate_failed",
                exc=exc,
            )
            return
        await self._publish(
            RobotEvent(
                source="runtime",
                event_type="adapter_deactivated",
                status=adapter.adapter_id,
            )
        )

    async def _refresh_adapter_state(self, adapter: RobotAdapter) -> None:
        self.state_store.set_adapter_state(await adapter.state())
        self.blackboard.update_robot_state(self.state_store.snapshot().state)
        await self._publish_state_snapshot()

    async def _record_adapter_lifecycle_error(
        self,
        adapter_id: str,
        *,
        event_type: str,
        exc: Exception,
    ) -> None:
        previous = self.state_store.snapshot().state.adapter_states.get(adapter_id)
        self.state_store.set_adapter_state(
            AdapterState(
                adapter_id=adapter_id,
                lifecycle=LifecycleState.ERROR,
                mode=previous.mode if previous is not None else self.config.mode,
                error=f"{type(exc).__name__}: {exc}",
            )
        )
        self.blackboard.update_robot_state(self.state_store.snapshot().state)
        await self._publish_state_snapshot()
        await self._publish(
            RobotEvent(
                source="runtime",
                event_type=event_type,
                severity="error",
                status=adapter_id,
                reason=f"{type(exc).__name__}: {exc}",
            )
        )

    async def _track_active_command(
        self,
        command: RobotCommand,
        adapter: RobotAdapter,
        task: asyncio.Task[AdapterResult],
    ) -> None:
        self._active_commands[command.command_id] = _ActiveCommand(
            command=command,
            adapter=adapter,
            task=task,
        )
        self.state_store.set_active_commands(sorted(self._active_commands))
        self.blackboard.update_robot_state(self.state_store.snapshot().state)
        await self._publish_state_snapshot()

    async def _clear_active_command(self, command_id: str) -> None:
        self._active_commands.pop(command_id, None)
        self.state_store.set_active_commands(sorted(self._active_commands))
        self.blackboard.update_robot_state(self.state_store.snapshot().state)
        await self._publish_state_snapshot()

    def _apply_safety_profile(self) -> None:
        profile = self.config.safety_profiles.get(self.config.safety_profile)
        if profile is not None:
            self.safety.limits = {**profile.limits, **self.safety.limits}
        if self.safety.limits:
            self.state_store.set_safety_state(
                SafetyState(active_limits=dict(self.safety.limits))
            )


__all__ = ["RobotRuntime", "RobotRuntimeDependencies"]


def _event_from_adapter_result(
    result: AdapterResult,
    *,
    intent_id: str,
    turn_id: str | None,
    plan_id: str,
    behavior_node: str,
    selected_capability: str,
) -> RobotEvent:
    severity = "error" if result.status.value in {"failed", "timeout"} else "info"
    return RobotEvent(
        source="adapter",
        event_type="adapter_result",
        severity=severity,
        turn_id=turn_id,
        intent_id=intent_id,
        plan_id=plan_id,
        command_id=result.command_id,
        status=result.status.value,
        payload={
            **result.to_dict(),
            "behavior_node": behavior_node,
            "selected_capability": selected_capability,
        },
        reason=result.error_message,
    )


def _command_with_safety_envelope(
    command: RobotCommand,
    decision: SafetyDecision,
) -> RobotCommand:
    envelope = {
        **command.safety_envelope,
        "decision_id": decision.decision_id,
        "checked_at_ms": decision.checked_at_ms,
        "status": decision.status.value,
        "reasons": list(decision.reasons),
        "effective_limits": dict(decision.effective_limits),
        "operator_action_required": decision.operator_action_required,
    }
    return dataclass_replace(command, safety_envelope=envelope)


def _timeout_s(timeout_ms: int) -> float:
    if timeout_ms <= 0:
        return 0.0
    return max(float(timeout_ms) / 1000.0, 0.001)


def _behavior_node_from_plan(
    plan: BehaviorPlan,
    *,
    policy_name: str = "",
) -> str:
    value = plan.constraints.get("behavior_node")
    if value:
        return str(value)
    policy = str(plan.constraints.get("policy_name") or policy_name or "unknown_policy")
    return behavior_node_for_policy(policy)


def _matches_task_id(command: RobotCommand, task_id: str) -> bool:
    if task_id in {command.command_id, command.plan_id, command.step_id}:
        return True
    payload = command.payload
    target = payload.get("target")
    constraints = payload.get("constraints")
    values = [
        payload.get("task_id"),
        target.get("task_id") if isinstance(target, dict) else None,
        constraints.get("task_id") if isinstance(constraints, dict) else None,
    ]
    return task_id in {str(item) for item in values if item is not None}


def _matches_scheduled_task_id(plan: BehaviorPlan, task_id: str) -> bool:
    if task_id in {plan.plan_id, plan.intent_id}:
        return True
    values: list[object] = [plan.constraints.get("task_id")]
    for step in plan.steps:
        values.append(step.step_id)
        values.append(step.capability_query.get("task_id"))
        values.extend(_task_values_from_mapping(step.capability_query.get("payload")))
        values.extend(_task_values_from_mapping(step.capability_query.get("constraints")))
    return task_id in {str(item) for item in values if item is not None}


def _task_values_from_mapping(value: object) -> list[object]:
    if not isinstance(value, dict):
        return []
    target = value.get("target")
    constraints = value.get("constraints")
    return [
        value.get("task_id"),
        target.get("task_id") if isinstance(target, dict) else None,
        constraints.get("task_id") if isinstance(constraints, dict) else None,
    ]
