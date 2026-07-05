"""Tests for RobotRuntime timeline scheduler."""

from __future__ import annotations

from reachy_mini.robot_runtime.contracts import (
    BehaviorPlan,
    FallbackPolicy,
    PlanStatus,
    TimingAnchor,
)
from reachy_mini.robot_runtime.scheduler import TimelineScheduler


def test_scheduler_orders_ready_plans_by_priority() -> None:
    """Ready plans are ordered by priority."""
    scheduler = TimelineScheduler()
    low = BehaviorPlan(
        intent_id="i1",
        plan_id="low",
        priority=10,
        timing_anchor=TimingAnchor.SPEECH_START,
        channels=["face"],
    )
    high = BehaviorPlan(
        intent_id="i2",
        plan_id="high",
        priority=80,
        timing_anchor=TimingAnchor.SPEECH_START,
        channels=["gesture"],
    )

    scheduler.schedule(low, anchor_time_ms=1000, now_ms_value=900)
    scheduler.schedule(high, anchor_time_ms=1000, now_ms_value=900)

    ready = scheduler.ready(anchor="speech_start", now_ms_value=1000)

    assert [item.plan.plan_id for item in ready] == ["high", "low"]


def test_pop_ready_respects_channel_mutex() -> None:
    """Conflicting plans stay scheduled when their channel is occupied."""
    scheduler = TimelineScheduler()
    face_plan = BehaviorPlan(intent_id="i1", plan_id="face", channels=["face"])
    gesture_plan = BehaviorPlan(intent_id="i2", plan_id="gesture", channels=["gesture"])
    scheduler.schedule(face_plan, now_ms_value=10)
    scheduler.schedule(gesture_plan, now_ms_value=10)

    popped = scheduler.pop_ready(now_ms_value=10, occupied_channels={"face"})

    assert [item.plan.plan_id for item in popped] == ["gesture"]
    assert [item.plan.plan_id for item in scheduler.list_scheduled()] == ["face"]


def test_pop_ready_can_filter_to_auto_executable_anchors() -> None:
    """Runtime auto ticks can leave speech/task anchors scheduled until triggered."""
    scheduler = TimelineScheduler()
    idle = BehaviorPlan(intent_id="i1", plan_id="idle", channels=["face"])
    speech = BehaviorPlan(
        intent_id="i2",
        plan_id="speech",
        timing_anchor=TimingAnchor.SPEECH_START,
        channels=["gesture"],
    )
    scheduler.schedule(idle, now_ms_value=10)
    scheduler.schedule(speech, now_ms_value=10)

    popped = scheduler.pop_ready(
        now_ms_value=10,
        allowed_anchors={TimingAnchor.IDLE.value},
    )

    assert [item.plan.plan_id for item in popped] == ["idle"]
    assert [item.plan.plan_id for item in scheduler.list_scheduled()] == ["speech"]


def test_interrupt_plan_preempts_lower_priority_conflict_with_fallback() -> None:
    """Interrupt scheduling removes lower-priority conflicting plans with fallback status."""
    scheduler = TimelineScheduler()
    low = BehaviorPlan(
        intent_id="i1",
        plan_id="low",
        priority=10,
        channels=["gesture"],
        fallback_policy=FallbackPolicy.DEGRADE,
    )
    high = BehaviorPlan(
        intent_id="i2",
        plan_id="high",
        priority=90,
        timing_anchor=TimingAnchor.INTERRUPT,
        channels=["gesture"],
    )
    scheduler.schedule(low, now_ms_value=10)

    decision = scheduler.schedule_with_decision(high, now_ms_value=20)

    assert decision.scheduled.plan.plan_id == "high"
    assert [item.plan.plan_id for item in decision.interrupted] == ["low"]
    assert decision.interrupted[0].plan.status is PlanStatus.DEGRADED
    assert decision.interrupted[0].reason == "interrupted_by=high;fallback=degrade"
    assert [item.plan.plan_id for item in scheduler.list_scheduled()] == ["high"]


def test_interrupt_plan_keeps_non_interruptible_conflict_scheduled() -> None:
    """Non-interruptible plans are not removed by a later interrupt plan."""
    scheduler = TimelineScheduler()
    locked = BehaviorPlan(
        intent_id="i1",
        plan_id="locked",
        priority=10,
        channels=["gesture"],
        interruptible=False,
    )
    high = BehaviorPlan(
        intent_id="i2",
        plan_id="high",
        priority=90,
        timing_anchor=TimingAnchor.INTERRUPT,
        channels=["gesture"],
    )
    scheduler.schedule(locked, now_ms_value=10)

    decision = scheduler.schedule_with_decision(high, now_ms_value=20)

    assert decision.interrupted == ()
    assert [item.plan.plan_id for item in scheduler.list_scheduled()] == [
        "high",
        "locked",
    ]
