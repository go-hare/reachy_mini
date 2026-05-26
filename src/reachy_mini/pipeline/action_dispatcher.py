"""ActionRuntime facade used by SDK MCP action tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from reachy_mini.action_runtime import ActionExecutor, ActionResult, ActionSpec

from .frames import ActionResultFrame, InterruptFrame


class ActionDispatcher:
    """Run SDK MCP action requests through ActionExecutor and publish results."""

    def __init__(
        self,
        executor: ActionExecutor,
        *,
        publish: Callable[[ActionResultFrame], Awaitable[None]] | None = None,
    ) -> None:
        """Create an action facade for SDK MCP tool handlers."""
        self.executor = executor
        self._publish = publish

    async def run_action(self, spec: ActionSpec) -> ActionResult:
        """Execute one action spec and publish its result frame."""
        result = await self.executor.submit(spec)
        if self._publish is not None:
            await self._publish(ActionResultFrame.from_result(result))
        return result

    async def process(self, frame: object) -> list[object]:
        """Handle action interrupts from the pipeline."""
        if isinstance(frame, InterruptFrame) and frame.scope in {"actions", "all"}:
            self.executor.cancel()
        return []
