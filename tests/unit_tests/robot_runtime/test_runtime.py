"""Tests for RobotRuntime async facade."""

from __future__ import annotations

import asyncio

import pytest

from reachy_mini.robot_runtime import (
    AdapterResult,
    AdapterState,
    BehaviorPlan,
    Capability,
    EmbodiedIntent,
    FallbackPolicy,
    LifecycleState,
    PolicyServiceRequest,
    PolicyServiceResponse,
    RobotRuntime,
    RobotRuntimeConfig,
    RobotRuntimeDependencies,
    RuntimeMode,
    SafetyProfileConfig,
    SafetyRuleResult,
    SafetyStatus,
    TimingAnchor,
)
from reachy_mini.robot_runtime.adapters.mujoco import MujocoAdapter
from reachy_mini.robot_runtime.adapters.reachy import ReachyAdapter
from reachy_mini.robot_runtime.contracts import now_ms


class _FailingAdapter:
    """Adapter that raises during execution."""

    adapter_id = "failing"

    async def configure(self, config: dict[str, object]) -> None:
        """Configure fake adapter."""
        return None

    async def activate(self) -> None:
        """Activate fake adapter."""
        return None

    async def deactivate(self) -> None:
        """Deactivate fake adapter."""
        return None

    async def capabilities(self) -> list[Capability]:
        """Expose one greet capability."""
        return [
            Capability(
                capability_id="failing:greet",
                adapter_id=self.adapter_id,
                embodiment="test",
                modality="motion",
                channels=["gesture"],
                semantic_tags=["greet"],
            )
        ]

    async def execute(self, command) -> AdapterResult:
        """Raise to exercise runtime adapter boundary."""
        raise RuntimeError(f"boom:{command.command_id}")

    async def cancel(self, command_id: str, reason: str) -> AdapterResult:
        """Cancel fake command."""
        return AdapterResult(
            command_id=command_id,
            adapter_id=self.adapter_id,
            status="cancelled",
        )

    async def state(self) -> AdapterState:
        """Return fake state."""
        return AdapterState(adapter_id=self.adapter_id, lifecycle=LifecycleState.ACTIVE)


class _SlowAdapter:
    """Adapter that sleeps long enough to exercise command timeout handling."""

    adapter_id = "slow"

    def __init__(self) -> None:
        self.cancel_reasons: list[str] = []
        self.cancelled_by_wait_for = False

    async def configure(self, config: dict[str, object]) -> None:
        """Configure fake adapter."""
        return None

    async def activate(self) -> None:
        """Activate fake adapter."""
        return None

    async def deactivate(self) -> None:
        """Deactivate fake adapter."""
        return None

    async def capabilities(self) -> list[Capability]:
        """Expose one slow greet capability."""
        return [
            Capability(
                capability_id="slow:greet",
                adapter_id=self.adapter_id,
                embodiment="mujoco",
                modality="motion",
                channels=["gesture"],
                semantic_tags=["greet"],
            )
        ]

    async def execute(self, command) -> AdapterResult:
        """Sleep until wait_for cancels this command."""
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            self.cancelled_by_wait_for = True
            raise
        return AdapterResult(
            command_id=command.command_id,
            adapter_id=self.adapter_id,
            status="completed",
        )

    async def cancel(self, command_id: str, reason: str) -> AdapterResult:
        """Record timeout cancellation."""
        del command_id
        self.cancel_reasons.append(reason)
        return AdapterResult(
            command_id="cancelled",
            adapter_id=self.adapter_id,
            status="cancelled",
        )

    async def state(self) -> AdapterState:
        """Return fake simulation state."""
        return AdapterState(
            adapter_id=self.adapter_id,
            lifecycle=LifecycleState.ACTIVE,
            mode=RuntimeMode.SIMULATION,
        )


class _DelayRule:
    """Custom safety rule that delays matching commands."""

    def evaluate(self, command, state, capability):
        """Delay every command that reaches this rule."""
        del command, state, capability
        return SafetyRuleResult(
            status=SafetyStatus.DELAY,
            reasons=("operator_confirmation_required",),
            operator_action_required=True,
        )


class _FakeMini:
    """Fake Reachy Mini SDK object."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def goto_target(self, **kwargs: object) -> None:
        """Record SDK calls."""
        self.calls.append(dict(kwargs))

    def look_at_world(
        self,
        x: float,
        y: float,
        z: float,
        duration: float,
        perform_movement: bool,
    ) -> None:
        """Record look-at SDK calls."""
        self.calls.append(
            {
                "x": x,
                "y": y,
                "z": z,
                "duration": duration,
                "perform_movement": perform_movement,
            }
        )


class _FakeMujocoBackend:
    """Daemon-like MuJoCo backend used to verify safe idle no-op behavior."""

    def __init__(self) -> None:
        self.target_head_pose = None
        self.target_body_yaw = None
        self.target_antenna_joint_positions = None
        self.ik_required = False


class _LearnedBackend:
    """Fake learned policy backend that returns semantic actions only."""

    def __init__(self) -> None:
        self.requests: list[PolicyServiceRequest] = []

    def plan(self, request: PolicyServiceRequest) -> PolicyServiceResponse:
        """Return a body-agnostic semantic action."""
        self.requests.append(request)
        return PolicyServiceResponse(
            status="planned",
            reason="learned_fixture",
            actions=[
                {
                    "channel": "gesture",
                    "semantic_tags": ["greet"],
                }
            ],
        )


@pytest.mark.asyncio
async def test_runtime_configure_activate_and_stop() -> None:
    """RobotRuntime owns lifecycle transitions and state snapshots."""
    events = []

    async def publish(event):
        events.append(event)

    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION),
        dependencies=RobotRuntimeDependencies(publish_event=publish),
    )

    await runtime.start()
    await asyncio.sleep(0)
    await runtime.stop()

    assert runtime.snapshot().state.lifecycle is LifecycleState.INACTIVE
    assert [event.status for event in events if event.event_type == "lifecycle_transition"] == [
        "configuring",
        "inactive",
        "activating",
        "active",
        "deactivating",
        "inactive",
    ]


@pytest.mark.asyncio
async def test_runtime_start_and_stop_manage_registered_adapter_lifecycle() -> None:
    """Runtime lifecycle owns registered adapter activation and deactivation."""
    events = []

    async def publish(event):
        events.append(event)

    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION),
        dependencies=RobotRuntimeDependencies(publish_event=publish),
    )
    await runtime.register_adapter(MujocoAdapter())

    assert (
        runtime.snapshot().state.adapter_states["mujoco"].lifecycle
        is LifecycleState.INACTIVE
    )

    await runtime.start()
    await asyncio.sleep(0)

    assert (
        runtime.snapshot().state.adapter_states["mujoco"].lifecycle
        is LifecycleState.ACTIVE
    )

    await runtime.stop()

    assert (
        runtime.snapshot().state.adapter_states["mujoco"].lifecycle
        is LifecycleState.INACTIVE
    )
    assert "adapter_activated" in [event.event_type for event in events]
    assert "adapter_deactivated" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_runtime_registers_adapter_active_when_runtime_is_active() -> None:
    """Adapters added after runtime activation join the active lifecycle."""
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    await runtime.activate()

    await runtime.register_adapter(MujocoAdapter())

    assert (
        runtime.snapshot().state.adapter_states["mujoco"].lifecycle
        is LifecycleState.ACTIVE
    )


@pytest.mark.asyncio
async def test_runtime_accept_intent_publishes_trace_and_tree_consumes() -> None:
    """Accepted intents are traced and consumed by the tree."""
    events = []

    async def publish(event):
        events.append(event)

    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION),
        dependencies=RobotRuntimeDependencies(publish_event=publish),
    )
    await runtime.activate()
    intent = EmbodiedIntent(intent_type="greet", turn_id="turn_1", priority=70)

    event = await runtime.accept_intent(intent)
    status = runtime.tick_once()

    assert event.event_type == "intent_received"
    assert status == "SUCCESS"
    assert runtime.blackboard.snapshot()["active_plan"]["intent_id"] == intent.intent_id
    assert any(item.intent_id == intent.intent_id for item in events)


@pytest.mark.asyncio
async def test_runtime_executes_same_greet_intent_on_mujoco_adapter() -> None:
    """The same semantic intent can resolve to the MuJoCo simulation adapter."""
    events = []

    async def publish(event):
        events.append(event)

    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION),
        dependencies=RobotRuntimeDependencies(publish_event=publish),
    )
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())

    result_events = await runtime.handle_intent(EmbodiedIntent(intent_type="greet", turn_id="turn_1"))

    scheduled = next(event for event in result_events if event.event_type == "plan_scheduled")
    adapter_result = result_events[-1]
    assert adapter_result.event_type == "adapter_result"
    assert adapter_result.status == "completed"
    assert adapter_result.payload["adapter_id"] == "mujoco"
    assert scheduled.payload["policy_name"] == "social_policy"
    assert scheduled.payload["behavior_node"] == "RobotPolicyEngine/social_policy"
    assert any(event.event_type == "safety_decision" for event in events)


@pytest.mark.asyncio
async def test_runtime_sends_safety_envelope_to_adapter_command() -> None:
    """Adapter commands carry the SafetyDecision envelope that approved them."""
    commands = []
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )

    async def execute(command):
        commands.append(command)
        return {"safety_envelope": command.safety_envelope}

    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter(execute_command=execute))

    events = await runtime.handle_intent(
        EmbodiedIntent(intent_type="greet", modalities=["gesture"], turn_id="turn_1")
    )
    safety_event = next(event for event in events if event.event_type == "safety_decision")
    envelope = commands[0].safety_envelope

    assert envelope["decision_id"] == safety_event.payload["decision_id"]
    assert envelope["checked_at_ms"] == safety_event.payload["checked_at_ms"]
    assert envelope["status"] == "allow"
    assert envelope["reasons"] == []
    assert envelope["effective_limits"] == {}
    assert envelope["operator_action_required"] is False


@pytest.mark.asyncio
async def test_runtime_does_not_execute_adapter_when_safety_delays() -> None:
    """Delayed safety decisions are observable but do not reach adapters."""
    commands = []
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    runtime.safety.rules.append(_DelayRule())

    async def execute(command):
        commands.append(command)
        return {"unexpected": True}

    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter(execute_command=execute))

    events = await runtime.handle_intent(
        EmbodiedIntent(intent_type="greet", modalities=["gesture"], turn_id="turn_delay")
    )
    safety_event = next(event for event in events if event.event_type == "safety_decision")

    assert safety_event.status == "delay"
    assert safety_event.severity == "warning"
    assert safety_event.reason == "operator_confirmation_required"
    assert safety_event.payload["operator_action_required"] is True
    assert commands == []
    assert not any(event.event_type == "adapter_result" for event in events)


@pytest.mark.asyncio
async def test_runtime_speech_motion_degrades_to_adapter_safe_idle() -> None:
    """Speech-motion safety degrade reaches the adapter as safe_idle, not source motion."""
    backend = _FakeMujocoBackend()
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    await runtime.activate()
    runtime.state_store.set_speech_state("speaking")
    await runtime.register_adapter(MujocoAdapter(backend=backend))

    events = await runtime.handle_intent(
        EmbodiedIntent(intent_type="greet", modalities=["gesture"], turn_id="turn_safe")
    )
    safety_event = next(event for event in events if event.event_type == "safety_decision")
    adapter_result = events[-1]

    assert safety_event.status == "degrade"
    assert "speech_active_motion_degraded" in safety_event.payload["reasons"]
    assert adapter_result.event_type == "adapter_result"
    assert adapter_result.status == "completed"
    assert adapter_result.payload["telemetry"] == {
        "safe_idle": True,
        "source_capability": "mujoco:motion:greet",
    }
    assert backend.target_head_pose is None
    assert backend.ik_required is False


@pytest.mark.asyncio
async def test_handle_intent_keeps_future_plan_scheduled_until_ready() -> None:
    """Future timing anchors are not executed by the model-facing tool call early."""
    commands = []
    future_ms = now_ms() + 10_000
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )

    async def execute(command):
        commands.append(command)
        return {"command_id": command.command_id}

    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter(execute_command=execute))

    events = await runtime.handle_intent(
        EmbodiedIntent(
            intent_type="greet",
            modalities=["gesture"],
            constraints={"start_after_ms": future_ms},
            turn_id="turn_future",
        )
    )

    assert [event.event_type for event in events] == [
        "intent_received",
        "plan_scheduled",
    ]
    assert commands == []
    assert len(runtime.scheduler.list_scheduled()) == 1

    ready_events = await runtime.execute_ready(now_ms_value=future_ms)

    assert commands
    assert ready_events[-1].event_type == "adapter_result"
    assert ready_events[-1].turn_id == "turn_future"
    assert runtime.scheduler.list_scheduled() == []


@pytest.mark.asyncio
async def test_handle_intent_keeps_speech_anchor_scheduled_until_anchor_ready() -> None:
    """Speech anchors wait for an explicit anchor signal instead of auto execution."""
    commands = []
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )

    async def execute(command):
        commands.append(command)
        return {"command_id": command.command_id}

    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter(execute_command=execute))

    events = await runtime.handle_intent(
        EmbodiedIntent(
            intent_type="greet",
            modalities=["gesture"],
            timing_anchor=TimingAnchor.SPEECH_START,
            turn_id="turn_speech_anchor",
        )
    )

    assert [event.event_type for event in events] == [
        "intent_received",
        "plan_scheduled",
    ]
    assert commands == []
    assert runtime.scheduler.list_scheduled()[0].plan.timing_anchor is TimingAnchor.SPEECH_START

    ready_events = await runtime.execute_ready(anchor=TimingAnchor.SPEECH_START)

    assert commands
    assert ready_events[-1].event_type == "adapter_result"
    assert ready_events[-1].turn_id == "turn_speech_anchor"
    assert runtime.scheduler.list_scheduled() == []


@pytest.mark.asyncio
async def test_cancel_task_matches_scheduled_plan_task_id_payload() -> None:
    """Scheduled task plans can be cancelled by payload task_id before execution."""
    commands = []
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )

    async def execute(command):
        commands.append(command)
        return {"command_id": command.command_id}

    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter(execute_command=execute))

    await runtime.handle_intent(
        EmbodiedIntent(
            intent_type="greet",
            modalities=["gesture"],
            target={"task_id": "task_payload"},
            timing_anchor=TimingAnchor.SPEECH_START,
            turn_id="turn_cancel_scheduled",
        )
    )
    cancel_event = await runtime.cancel_task("task_payload", reason="operator")

    assert commands == []
    assert cancel_event.status == "accepted"
    assert cancel_event.payload["cancelled_plan_ids"]
    assert cancel_event.payload["active_command_ids"] == []
    assert runtime.scheduler.list_scheduled() == []


@pytest.mark.asyncio
async def test_handle_intent_returns_plan_rejected_when_deadline_has_passed() -> None:
    """Scheduler deadline failures are structured events, not Brain-turn exceptions."""
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())

    events = await runtime.handle_intent(
        EmbodiedIntent(
            intent_type="greet",
            modalities=["gesture"],
            constraints={"deadline_ms": 1},
            turn_id="turn_deadline",
        )
    )

    assert [event.event_type for event in events] == [
        "intent_received",
        "plan_rejected",
    ]
    rejected = events[-1]
    assert rejected.source == "scheduler"
    assert rejected.severity == "warning"
    assert rejected.status == "failed"
    assert rejected.turn_id == "turn_deadline"
    assert rejected.reason == "plan cannot be scheduled after deadline_ms"
    assert rejected.payload["rejection_reason"] == (
        "plan cannot be scheduled after deadline_ms"
    )
    assert rejected.payload["deadline_ms"] == 1
    assert rejected.payload["policy_name"] == "social_policy"
    assert rejected.payload["timing_anchor"] == "idle"
    assert rejected.payload["behavior_node"] == "RobotPolicyEngine/social_policy"
    assert runtime.scheduler.list_scheduled() == []
    assert runtime.metrics().completed_intent_count == 1
    assert runtime.metrics().plan_rejection_count == 1
    assert runtime.metrics().plan_rejections_by_reason == {
        "plan cannot be scheduled after deadline_ms": 1
    }
    assert runtime.metrics().plan_rejections_by_policy == {"social_policy": 1}
    assert runtime.metrics().plan_rejections_by_timing_anchor == {"idle": 1}


@pytest.mark.asyncio
async def test_runtime_records_capability_unresolved_reason_breakdown() -> None:
    """Resolver failures expose structured reason breakdown metrics."""
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    await runtime.activate()

    events = await runtime.handle_intent(
        EmbodiedIntent(intent_type="greet", modalities=["gesture"], turn_id="turn_no_cap")
    )
    unresolved = events[-1]
    metrics = runtime.metrics()

    assert unresolved.event_type == "capability_unresolved"
    assert unresolved.status == "unresolved"
    assert unresolved.reason == "no_capability_match"
    assert unresolved.payload["reasons"] == ["no_capability_match"]
    assert unresolved.payload["behavior_node"] == "RobotPolicyEngine/social_policy"
    assert unresolved.payload["step_id"]
    assert metrics.capability_unresolved_count == 1
    assert metrics.capability_unresolved_by_reason == {"no_capability_match": 1}
    assert metrics.capability_unresolved_by_behavior_node == {
        "RobotPolicyEngine/social_policy": 1
    }


@pytest.mark.asyncio
async def test_runtime_records_adapter_missing_breakdown() -> None:
    """A missing adapter after capability resolution is observable by adapter id."""
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    runtime.dependencies.adapters.pop("mujoco")

    events = await runtime.handle_intent(
        EmbodiedIntent(intent_type="greet", modalities=["gesture"], turn_id="turn_missing")
    )
    missing = events[-1]
    metrics = runtime.metrics()

    assert missing.event_type == "adapter_missing"
    assert missing.status == "mujoco"
    assert missing.payload["adapter_id"] == "mujoco"
    assert missing.payload["capability_id"] == "mujoco:motion:greet"
    assert missing.payload["selected_capability"] == "mujoco:motion:greet"
    assert metrics.adapter_missing_count == 1
    assert metrics.adapter_missing_by_adapter == {"mujoco": 1}


@pytest.mark.asyncio
async def test_runtime_stop_cancels_future_scheduled_plans() -> None:
    """Stopping Runtime removes pending future plans before they can run later."""
    commands = []
    events = []
    future_ms = now_ms() + 10_000

    async def publish(event):
        events.append(event)

    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION),
        dependencies=RobotRuntimeDependencies(publish_event=publish),
    )

    async def execute(command):
        commands.append(command)
        return {"command_id": command.command_id}

    await runtime.start()
    await runtime.register_adapter(MujocoAdapter(execute_command=execute))
    await runtime.handle_intent(
        EmbodiedIntent(
            intent_type="greet",
            modalities=["gesture"],
            constraints={"start_after_ms": future_ms},
            turn_id="turn_future_stop",
        )
    )

    assert commands == []
    assert len(runtime.scheduler.list_scheduled()) == 1

    await runtime.stop()
    await runtime.execute_ready(now_ms_value=future_ms)

    assert commands == []
    assert runtime.scheduler.list_scheduled() == []
    assert any(
        event.event_type == "scheduled_plans_cancel_requested"
        and event.payload["plan_ids"]
        for event in events
    )


@pytest.mark.asyncio
async def test_tick_loop_executes_due_scheduled_plan() -> None:
    """The runtime tick loop advances scheduled plans once their deadline arrives."""
    commands = []
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=200, mode=RuntimeMode.SIMULATION)
    )

    async def execute(command):
        commands.append(command)
        return {"command_id": command.command_id}

    await runtime.register_adapter(MujocoAdapter(execute_command=execute))
    await runtime.start()
    try:
        await runtime.handle_intent(
            EmbodiedIntent(
                intent_type="greet",
                modalities=["gesture"],
                constraints={"start_after_ms": now_ms() + 20},
                turn_id="turn_tick",
            )
        )
        for _ in range(100):
            if commands:
                break
            await asyncio.sleep(0.005)
    finally:
        await runtime.stop()

    assert commands
    assert runtime.trace_timeline(turn_id="turn_tick").complete is True


@pytest.mark.asyncio
async def test_runtime_trace_timeline_links_intent_to_adapter_result() -> None:
    """Runtime telemetry can reconstruct the full intent-plan-command chain."""
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    intent = EmbodiedIntent(
        intent_type="greet",
        modalities=["gesture"],
        turn_id="turn_trace",
    )

    await runtime.handle_intent(intent)
    timeline = runtime.trace_timeline(intent_id=intent.intent_id)

    assert timeline.complete is True
    assert timeline.turn_ids == ("turn_trace",)
    assert timeline.intent_ids == (intent.intent_id,)
    assert len(timeline.plan_ids) == 1
    assert len(timeline.command_ids) == 1
    assert timeline.adapter_ids == ("mujoco",)
    assert timeline.event_types == (
        "intent_received",
        "plan_scheduled",
        "command_resolved",
        "safety_decision",
        "command_started",
        "adapter_result",
    )
    scheduled = next(event for event in timeline.events if event.event_type == "plan_scheduled")
    resolved = next(event for event in timeline.events if event.event_type == "command_resolved")
    started = next(event for event in timeline.events if event.event_type == "command_started")
    safety_event = next(event for event in timeline.events if event.event_type == "safety_decision")
    adapter_result = next(event for event in timeline.events if event.event_type == "adapter_result")
    assert scheduled.payload["behavior_node"] == "RobotPolicyEngine/social_policy"
    assert resolved.payload["behavior_node"] == "RobotPolicyEngine/social_policy"
    assert resolved.payload["selected_capability"] == "mujoco:motion:greet"
    assert safety_event.payload["behavior_node"] == "RobotPolicyEngine/social_policy"
    assert safety_event.payload["selected_capability"] == "mujoco:motion:greet"
    assert started.status == "running"
    assert started.payload["adapter_id"] == "mujoco"
    assert started.payload["behavior_node"] == "RobotPolicyEngine/social_policy"
    assert started.payload["capability_id"] == "mujoco:motion:greet"
    assert started.payload["command_type"] == "play_motion"
    assert started.payload["selected_capability"] == "mujoco:motion:greet"
    assert started.payload["safety_decision_id"] == safety_event.payload["decision_id"]
    assert adapter_result.payload["behavior_node"] == "RobotPolicyEngine/social_policy"
    assert adapter_result.payload["selected_capability"] == "mujoco:motion:greet"


@pytest.mark.asyncio
async def test_runtime_metrics_count_success_and_adapter_failure() -> None:
    """Runtime metrics summarize successful and failed adapter executions."""
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    await runtime.handle_intent(
        EmbodiedIntent(intent_type="greet", modalities=["gesture"], turn_id="turn_ok")
    )
    await runtime.register_adapter(_FailingAdapter())
    await runtime.handle_intent(
        EmbodiedIntent(
            intent_type="greet",
            modalities=["gesture"],
            constraints={"adapter_id": "failing"},
            turn_id="turn_fail",
        )
    )

    metrics = runtime.metrics()

    assert metrics.intent_count == 2
    assert metrics.completed_intent_count == 2
    assert metrics.adapter_success_count == 1
    assert metrics.adapter_failure_count == 1
    assert metrics.max_latency_ms is not None


@pytest.mark.asyncio
async def test_runtime_executes_same_greet_intent_on_reachy_dry_run_adapter() -> None:
    """The same semantic intent can resolve to Reachy hardware in dry-run mode."""
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.HARDWARE)
    )
    await runtime.activate()
    await runtime.register_adapter(ReachyAdapter())

    result_events = await runtime.handle_intent(
        EmbodiedIntent(intent_type="greet", modalities=["gesture"], turn_id="turn_1")
    )

    adapter_result = result_events[-1]
    assert adapter_result.event_type == "adapter_result"
    assert adapter_result.status == "completed"
    assert adapter_result.payload["adapter_id"] == "reachy"
    assert adapter_result.payload["telemetry"] == {
        "dry_run": True,
        "command_type": "play_motion",
    }


@pytest.mark.asyncio
async def test_runtime_converts_adapter_exception_to_failed_event() -> None:
    """Adapter exceptions become AdapterResult and RobotEvent failures."""
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.HARDWARE)
    )
    await runtime.activate()
    await runtime.register_adapter(_FailingAdapter())

    events = await runtime.handle_intent(
        EmbodiedIntent(intent_type="greet", modalities=["gesture"], turn_id="turn_1")
    )

    adapter_result = events[-1]

    assert adapter_result.event_type == "adapter_result"
    assert adapter_result.severity == "error"
    assert adapter_result.status == "failed"
    assert adapter_result.payload["error_code"] == "RuntimeError"
    assert "boom:" in adapter_result.reason


@pytest.mark.asyncio
async def test_runtime_times_out_and_cancels_slow_adapter_command() -> None:
    """Command timeout is an execution boundary, not just metadata."""
    adapter = _SlowAdapter()
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    await runtime.activate()
    await runtime.register_adapter(adapter)

    events = await runtime.handle_intent(
        EmbodiedIntent(
            intent_type="greet",
            modalities=["gesture"],
            constraints={"adapter_id": "slow", "timeout_ms": 1},
            turn_id="turn_timeout",
        )
    )
    adapter_result = events[-1]

    assert adapter.cancelled_by_wait_for is True
    assert adapter.cancel_reasons == ["command_timeout"]
    assert adapter_result.event_type == "adapter_result"
    assert adapter_result.status == "timeout"
    assert adapter_result.severity == "error"
    assert adapter_result.payload["error_code"] == "command_timeout"
    assert adapter_result.payload["telemetry"] == {"timeout_ms": 1}


@pytest.mark.asyncio
async def test_runtime_tracks_and_cancels_active_adapter_command() -> None:
    """Active commands are observable and cancellable while adapter work is running."""
    adapter = _SlowAdapter()
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    await runtime.activate()
    await runtime.register_adapter(adapter)

    handle_task = asyncio.create_task(
        runtime.handle_intent(
            EmbodiedIntent(
                intent_type="greet",
                modalities=["gesture"],
                constraints={"adapter_id": "slow", "timeout_ms": 5000},
                turn_id="turn_cancel",
            )
        )
    )
    command_id = await _wait_for_active_command(runtime)

    cancel_event = await runtime.cancel_task(command_id, reason="operator_cancelled")
    events = await handle_task
    adapter_result = events[-1]

    assert cancel_event.status == "accepted"
    assert cancel_event.payload["active_command_ids"] == [command_id]
    assert adapter.cancel_reasons == ["operator_cancelled"]
    assert adapter_result.event_type == "adapter_result"
    assert adapter_result.status == "cancelled"
    assert adapter_result.reason == "operator_cancelled"
    assert runtime.snapshot().state.active_commands == []
    assert runtime.blackboard.snapshot()["active_commands"] == []


@pytest.mark.asyncio
async def test_runtime_stop_cancels_active_adapter_commands() -> None:
    """Stopping Runtime cancels in-flight adapter work before deactivation."""
    adapter = _SlowAdapter()
    events = []

    async def publish(event):
        events.append(event)

    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION),
        dependencies=RobotRuntimeDependencies(publish_event=publish),
    )
    await runtime.start()
    await runtime.register_adapter(adapter)

    handle_task = asyncio.create_task(
        runtime.handle_intent(
            EmbodiedIntent(
                intent_type="greet",
                modalities=["gesture"],
                constraints={"adapter_id": "slow", "timeout_ms": 5000},
                turn_id="turn_stop",
            )
        )
    )
    command_id = await _wait_for_active_command(runtime)

    await runtime.stop()
    result_events = await handle_task
    adapter_result = result_events[-1]

    assert adapter.cancel_reasons == ["runtime_stop"]
    assert adapter_result.event_type == "adapter_result"
    assert adapter_result.status == "cancelled"
    assert adapter_result.reason == "runtime_stop"
    assert runtime.snapshot().state.active_commands == []
    assert runtime.blackboard.snapshot()["active_commands"] == []
    assert any(
        event.event_type == "active_commands_cancel_requested"
        and event.payload["active_command_ids"] == [command_id]
        for event in events
    )


@pytest.mark.asyncio
async def test_runtime_safety_denies_reachy_command_when_not_active() -> None:
    """Hardware commands are not sent when Runtime lifecycle is not active."""
    mini = _FakeMini()
    runtime = RobotRuntime(config=RobotRuntimeConfig(tick_hz=100))
    await runtime.register_adapter(ReachyAdapter(mini=mini), config={"dry_run": False})

    events = await runtime.handle_intent(
        EmbodiedIntent(intent_type="greet", modalities=["gesture"], turn_id="turn_1")
    )

    safety_event = next(event for event in events if event.event_type == "safety_decision")

    assert safety_event.status == "deny"
    assert "runtime_not_active" in safety_event.reason
    assert not any(event.event_type == "adapter_result" for event in events)
    assert mini.calls == []


async def _wait_for_active_command(runtime: RobotRuntime) -> str:
    for _ in range(100):
        active = runtime.snapshot().state.active_commands
        if active:
            assert runtime.blackboard.snapshot()["active_commands"] == active
            return active[0]
        await asyncio.sleep(0.001)
    raise AssertionError("active command was not observed")


@pytest.mark.asyncio
async def test_runtime_applies_configured_safety_profile_to_adapter_command() -> None:
    """Configured safety profile limits are enforced before adapter execution."""
    commands = []
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(
            tick_hz=100,
            mode=RuntimeMode.SIMULATION,
            safety_profile="simulation",
            safety_profiles={
                "simulation": SafetyProfileConfig(
                    name="simulation",
                    limits={"max_timeout_ms": 250},
                )
            },
        )
    )

    async def execute(command):
        commands.append(command)
        return {"timeout_ms": command.timeout_ms}

    assert runtime.safety.limits == {"max_timeout_ms": 250}
    assert runtime.snapshot().state.safety_state.active_limits == {"max_timeout_ms": 250}

    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter(execute_command=execute))

    events = await runtime.handle_intent(
        EmbodiedIntent(intent_type="greet", modalities=["gesture"], turn_id="turn_1")
    )
    safety_event = next(event for event in events if event.event_type == "safety_decision")

    assert safety_event.status == "degrade"
    assert safety_event.payload["effective_limits"] == {"max_timeout_ms": 250}
    assert commands[0].timeout_ms == 250
    assert commands[0].safety_envelope["decision_id"] == safety_event.payload["decision_id"]
    assert commands[0].safety_envelope["status"] == "degrade"
    assert commands[0].safety_envelope["reasons"] == ["timeout_limited"]
    assert commands[0].safety_envelope["effective_limits"] == {"max_timeout_ms": 250}


@pytest.mark.asyncio
async def test_runtime_publishes_interrupted_plan_event() -> None:
    """Interrupt intents produce observable scheduler events before execution."""
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(tick_hz=100, mode=RuntimeMode.SIMULATION)
    )
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    runtime.scheduler.schedule(
        BehaviorPlan(
            intent_id="old_intent",
            plan_id="old_plan",
            priority=10,
            channels=["gesture"],
            fallback_policy=FallbackPolicy.DEGRADE,
        ),
        now_ms_value=10,
    )

    events = await runtime.handle_intent(
        EmbodiedIntent(
            intent_type="greet",
            modalities=["gesture"],
            priority=90,
            timing_anchor=TimingAnchor.INTERRUPT,
            turn_id="turn_1",
        )
    )

    interrupted = next(event for event in events if event.event_type == "plan_interrupted")
    adapter_result = events[-1]

    assert interrupted.source == "scheduler"
    assert interrupted.intent_id == "old_intent"
    assert interrupted.plan_id == "old_plan"
    assert interrupted.status == "degraded"
    assert "fallback=degrade" in interrupted.reason
    assert adapter_result.event_type == "adapter_result"
    assert adapter_result.status == "completed"


@pytest.mark.asyncio
async def test_runtime_learned_policy_backend_still_flows_through_safety() -> None:
    """Learned policy actions are resolved and denied by safety before hardware execution."""
    mini = _FakeMini()
    backend = _LearnedBackend()
    runtime = RobotRuntime(
        config=RobotRuntimeConfig(mode=RuntimeMode.HARDWARE),
        dependencies=RobotRuntimeDependencies(policy_backend=backend),
    )
    await runtime.register_adapter(ReachyAdapter(mini=mini), config={"dry_run": False})

    events = await runtime.handle_intent(
        EmbodiedIntent(
            intent_type="greet",
            constraints={"policy_backend": "openpi"},
            turn_id="turn_learned",
        )
    )
    scheduled = next(event for event in events if event.event_type == "plan_scheduled")
    safety_event = next(event for event in events if event.event_type == "safety_decision")

    assert backend.requests[0].intent.turn_id == "turn_learned"
    assert scheduled.payload["policy_name"] == "learned_policy"
    assert safety_event.status == "deny"
    assert "runtime_not_active" in safety_event.reason
    assert not any(event.event_type == "adapter_result" for event in events)
    assert mini.calls == []
