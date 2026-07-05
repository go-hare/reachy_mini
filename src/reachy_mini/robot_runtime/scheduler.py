"""Timeline scheduler for body-agnostic behavior plans."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field, replace

from reachy_mini.robot_runtime.contracts import (
    BehaviorPlan,
    FallbackPolicy,
    PlanStatus,
    TimingAnchor,
    now_ms,
)


@dataclass(frozen=True, slots=True)
class ScheduledPlan:
    """A plan accepted by the timeline scheduler."""

    plan: BehaviorPlan
    scheduled_at_ms: int = field(default_factory=now_ms)
    ready_at_ms: int = field(default_factory=now_ms)
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ScheduleDecision:
    """Scheduler decision including plans displaced by the new plan."""

    scheduled: ScheduledPlan
    interrupted: tuple[ScheduledPlan, ...] = ()


@dataclass(slots=True)
class TimelineScheduler:
    """Priority scheduler with timing anchors and channel mutex filtering."""

    _scheduled: dict[str, ScheduledPlan] = field(default_factory=dict)

    def schedule(
        self,
        plan: BehaviorPlan,
        *,
        anchor_time_ms: int | None = None,
        now_ms_value: int | None = None,
        allow_interrupt: bool | None = None,
    ) -> ScheduledPlan:
        """Schedule a behavior plan and return its scheduled record."""
        return self.schedule_with_decision(
            plan,
            anchor_time_ms=anchor_time_ms,
            now_ms_value=now_ms_value,
            allow_interrupt=allow_interrupt,
        ).scheduled

    def schedule_with_decision(
        self,
        plan: BehaviorPlan,
        *,
        anchor_time_ms: int | None = None,
        now_ms_value: int | None = None,
        allow_interrupt: bool | None = None,
    ) -> ScheduleDecision:
        """Schedule a plan and report any lower-priority interrupted plans."""
        current_ms = now_ms_value if now_ms_value is not None else now_ms()
        base_ms = anchor_time_ms if anchor_time_ms is not None else current_ms
        ready_at_ms = max(base_ms, plan.start_after_ms or base_ms)
        if plan.deadline_ms is not None and ready_at_ms > plan.deadline_ms:
            raise ValueError("plan cannot be scheduled after deadline_ms")
        should_interrupt = (
            allow_interrupt
            if allow_interrupt is not None
            else plan.timing_anchor is TimingAnchor.INTERRUPT
        )
        interrupted = (
            self._interrupt_conflicts(plan, current_ms=current_ms)
            if should_interrupt
            else ()
        )
        scheduled = ScheduledPlan(
            plan=replace(plan, status=PlanStatus.SCHEDULED),
            scheduled_at_ms=current_ms,
            ready_at_ms=ready_at_ms,
            reason=f"anchor={plan.timing_anchor.value}",
        )
        self._scheduled[plan.plan_id] = scheduled
        return ScheduleDecision(scheduled=scheduled, interrupted=interrupted)

    def ready(
        self,
        *,
        anchor: TimingAnchor | str | None = None,
        now_ms_value: int | None = None,
        allowed_anchors: Collection[str] | None = None,
    ) -> list[ScheduledPlan]:
        """Return due plans sorted by priority and ready time."""
        current_ms = now_ms_value if now_ms_value is not None else now_ms()
        anchor_value = _anchor_value(anchor)
        allowed_values = set(allowed_anchors or ())
        items = [
            item
            for item in self._scheduled.values()
            if item.ready_at_ms <= current_ms
            and (anchor_value is None or item.plan.timing_anchor.value == anchor_value)
            and (
                anchor_value is not None
                or not allowed_values
                or item.plan.timing_anchor.value in allowed_values
            )
        ]
        return sorted(items, key=lambda item: (-item.plan.priority, item.ready_at_ms))

    def pop_ready(
        self,
        *,
        anchor: TimingAnchor | str | None = None,
        now_ms_value: int | None = None,
        occupied_channels: set[str] | None = None,
        allowed_anchors: Collection[str] | None = None,
    ) -> list[ScheduledPlan]:
        """Pop due plans that do not conflict with occupied channels."""
        used = set(occupied_channels or set())
        selected: list[ScheduledPlan] = []
        for item in self.ready(
            anchor=anchor,
            now_ms_value=now_ms_value,
            allowed_anchors=allowed_anchors,
        ):
            channels = set(item.plan.channels)
            if channels & used:
                continue
            selected.append(item)
            used.update(channels)
        for item in selected:
            self._scheduled.pop(item.plan.plan_id, None)
        return selected

    def cancel(self, plan_id: str, *, reason: str = "") -> ScheduledPlan | None:
        """Remove one plan from the scheduler."""
        scheduled = self._scheduled.pop(plan_id, None)
        if scheduled is None:
            return None
        return ScheduledPlan(
            plan=replace(scheduled.plan, status=PlanStatus.CANCELLED),
            scheduled_at_ms=scheduled.scheduled_at_ms,
            ready_at_ms=scheduled.ready_at_ms,
            reason=reason or "cancelled",
        )

    def list_scheduled(self) -> list[ScheduledPlan]:
        """Return all scheduled plans in deterministic order."""
        return sorted(self._scheduled.values(), key=lambda item: item.plan.plan_id)

    def _interrupt_conflicts(
        self,
        incoming: BehaviorPlan,
        *,
        current_ms: int,
    ) -> tuple[ScheduledPlan, ...]:
        incoming_channels = set(incoming.channels)
        if not incoming_channels:
            return ()
        interrupted: list[ScheduledPlan] = []
        for scheduled in list(self._scheduled.values()):
            if not incoming_channels & set(scheduled.plan.channels):
                continue
            if not scheduled.plan.interruptible:
                continue
            if scheduled.plan.priority >= incoming.priority:
                continue
            self._scheduled.pop(scheduled.plan.plan_id, None)
            interrupted.append(
                ScheduledPlan(
                    plan=replace(
                        scheduled.plan,
                        status=_fallback_status(scheduled.plan.fallback_policy),
                    ),
                    scheduled_at_ms=scheduled.scheduled_at_ms,
                    ready_at_ms=current_ms,
                    reason=(
                        f"interrupted_by={incoming.plan_id};"
                        f"fallback={scheduled.plan.fallback_policy.value}"
                    ),
                )
            )
        return tuple(interrupted)


def _anchor_value(anchor: TimingAnchor | str | None) -> str | None:
    if anchor is None:
        return None
    if isinstance(anchor, TimingAnchor):
        return anchor.value
    return TimingAnchor(str(anchor)).value


def _fallback_status(fallback: FallbackPolicy | str) -> PlanStatus:
    policy = fallback if isinstance(fallback, FallbackPolicy) else FallbackPolicy(str(fallback))
    if policy is FallbackPolicy.DEGRADE:
        return PlanStatus.DEGRADED
    return PlanStatus.CANCELLED


__all__ = ["ScheduleDecision", "ScheduledPlan", "TimelineScheduler"]
