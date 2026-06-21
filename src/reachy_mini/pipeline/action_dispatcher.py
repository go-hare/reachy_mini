"""ActionRuntime facade used by SDK MCP action tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from reachy_mini.action_runtime import ActionExecutor, ActionResult, ActionSpec

from .frames import ActionResultFrame, EmbodimentFrame, InterruptFrame


class ActionDispatcher:
    """Run SDK MCP action requests through ActionExecutor and publish results."""

    def __init__(
        self,
        executor: ActionExecutor,
        *,
        publish: Callable[[object], Awaitable[None]] | None = None,
    ) -> None:
        """Create an action facade for SDK MCP tool handlers."""
        self.executor = executor
        self._publish = publish

    async def run_action(self, spec: ActionSpec) -> ActionResult:
        """Execute one action spec and publish its result frame."""
        result = await self.executor.submit(spec)
        if self._publish is not None:
            for frame in self._frames_from_result(result):
                await self._publish(frame)
        return result

    async def process(self, frame: object) -> list[object]:
        """Handle action interrupts from the pipeline."""
        if isinstance(frame, InterruptFrame) and frame.scope in {"actions", "all"}:
            self.executor.cancel()
        return []

    def _frames_from_result(self, result: ActionResult) -> list[ActionResultFrame | EmbodimentFrame]:
        frames: list[ActionResultFrame | EmbodimentFrame] = []
        if result.status == "ok":
            embodiment = _result_embodiment_payload(result.result)
            if embodiment is not None:
                frames.append(
                    EmbodimentFrame(
                        action=str(embodiment.get("action") or ""),
                        target=str(embodiment.get("target") or "all"),
                        payload=dict(embodiment.get("payload") or {}),
                    )
                )
        frames.append(ActionResultFrame.from_result(result))
        return frames


def _result_embodiment_payload(result: object) -> dict[str, object] | None:
    if not isinstance(result, dict):
        return None
    payload = result.get("embodiment")
    if not isinstance(payload, dict):
        return None
    return payload
