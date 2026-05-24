"""Dispatch Brain action specs to the v4 ActionExecutor."""

from __future__ import annotations

from reachy_mini.action_runtime import ActionExecutor

from .frames import (
    ActionResultFrame,
    ActionSpecFrame,
    BrainReplyFrame,
    InterruptFrame,
)


class ActionDispatcher:
    """Pipeline adapter for ActionExecutor."""

    def __init__(self, executor: ActionExecutor) -> None:
        """Create an action dispatcher."""
        self.executor = executor

    async def process(self, frame: object) -> list[object]:
        """Process BrainReplyFrame, ActionSpecFrame, or InterruptFrame."""
        if isinstance(frame, BrainReplyFrame):
            return [
                ActionSpecFrame(spec=spec, turn_id=frame.turn_id)
                for spec in frame.actions
            ]

        if isinstance(frame, ActionSpecFrame):
            try:
                result = await self.executor.submit(frame.spec)
            except Exception as exc:
                return [
                    ActionResultFrame(
                        request_id=frame.spec.request_id,
                        name=frame.spec.name,
                        status="error",
                        error=f"{type(exc).__name__}: {exc}",
                        duration_ms=0,
                    )
                ]
            return [ActionResultFrame.from_result(result)]

        if isinstance(frame, InterruptFrame) and frame.scope in {"actions", "all"}:
            self.executor.cancel()
            return []

        return []
