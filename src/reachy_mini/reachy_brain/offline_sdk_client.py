"""Explicit offline Claude Agent SDK client for tests and smoke runs."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from reachy_mini.action_runtime import ActionSpec

from .mcp_server import ActionRunner


class OfflineSDKClient:
    """Deterministic SDK-message client used only when explicitly injected."""

    def __init__(
        self,
        _options: Any = None,
        *,
        action_runner: ActionRunner | None = None,
    ) -> None:
        """Create an offline SDK client."""
        self.action_runner = action_runner
        self.prompts: list[tuple[str, str]] = []
        self.connected = False
        self.interrupted = False
        self.stopped_tasks: list[str] = []

    async def connect(self) -> None:
        """Mark the fake SDK client connected."""
        self.connected = True

    async def query(self, prompt: str, session_id: str = "default") -> None:
        """Store one prompt for the next response stream."""
        self.prompts.append((prompt, session_id))

    async def receive_response(self) -> AsyncIterator[Any]:
        """Yield SDK-native messages for the stored prompt."""
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            TaskNotificationMessage,
            TaskProgressMessage,
            TaskStartedMessage,
            TextBlock,
        )

        prompt, session_id = self.prompts[-1] if self.prompts else ("", "default")
        user_text = prompt.split("\n", 1)[0].strip()
        normalized = user_text.lower()
        if any(keyword in normalized for keyword in ("巡视", "patrol")):
            task_id = f"offline_task_{uuid.uuid4().hex[:8]}"
            yield AssistantMessage(
                content=[TextBlock(text="我开始巡视，过程中你仍然可以继续和我说话。")],
                model="offline-sdk-smoke",
                session_id=session_id,
            )
            yield TaskStartedMessage(
                subtype="task_started",
                data={"source": "offline_sdk"},
                task_id=task_id,
                description="offline patrol",
                uuid=str(uuid.uuid4()),
                session_id=session_id,
            )
            yield TaskProgressMessage(
                subtype="task_progress",
                data={"source": "offline_sdk"},
                task_id=task_id,
                description="offline patrol",
                usage={"total_tokens": 0, "tool_uses": 0, "duration_ms": 0},
                uuid=str(uuid.uuid4()),
                session_id=session_id,
            )
            yield TaskNotificationMessage(
                subtype="task_notification",
                data={"source": "offline_sdk"},
                task_id=task_id,
                status="completed",
                output_file="",
                summary="offline patrol complete",
                uuid=str(uuid.uuid4()),
                session_id=session_id,
            )
        else:
            reply = f"我听到了：{user_text}" if user_text else "我在。"
            yield AssistantMessage(
                content=[TextBlock(text=reply)],
                model="offline-sdk-smoke",
                session_id=session_id,
            )
            if self.action_runner is not None:
                await self.action_runner(
                    ActionSpec(
                        name="nod",
                        params={"cycles": 1, "period_s": 0.3},
                        reason="offline_sdk_ack",
                        request_id=f"offline_{uuid.uuid4().hex}",
                        owner_id="main-agent",
                    )
                )
        yield ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id=session_id,
        )

    async def interrupt(self) -> None:
        """Record an interrupt call."""
        self.interrupted = True

    async def stop_task(self, task_id: str) -> None:
        """Record a task stop call."""
        self.stopped_tasks.append(task_id)

    async def disconnect(self) -> None:
        """Mark the fake SDK client disconnected."""
        self.connected = False
