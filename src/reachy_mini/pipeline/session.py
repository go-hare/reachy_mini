"""Long-lived SDK-first v4 runtime session."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from reachy_mini.action_runtime import ActionExecutor
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.action_runtime.registry import ActionRegistry
from reachy_mini.reachy_brain.agent import BrainAgent, ClientFactory
from reachy_mini.reachy_brain.config import AgentConfig, from_profile
from reachy_mini.reachy_brain.pipecat_bridge import sdk_message_to_worker_event

from .action_dispatcher import ActionDispatcher
from .brain_processor import BrainProcessor
from .frames import (
    AudioFrame,
    BrowserInputFrame,
    InterruptFrame,
    SDKMessageFrame,
    SpeechActivityFrame,
    SpeechPresenterFrame,
    TranscriptionFrame,
    TTSAudioFrame,
    TTSStopFrame,
    VisionEventFrame,
    WorkerEventFrame,
)
from .output_bus import OutputBus, OutputSubscription
from .speech_presenter import SpeechPresenter
from .tts_kokoro import KokoroAdapter

LOGGER = logging.getLogger(__name__)

SURFACE_TASK_ID = "__surface__"


class Clock(Protocol):
    """Minimal clock protocol used by the session for tests."""

    def monotonic(self) -> float:
        """Return monotonic seconds."""


class _SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()


class _NoopMini:
    """Fallback SDK object used when no hardware factory is supplied."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def goto_target(self, **kwargs: Any) -> None:
        self.calls.append({"method": "goto_target", "kwargs": kwargs})

    def look_at_world(
        self,
        x: float,
        y: float,
        z: float,
        duration: float,
        perform_movement: bool,
    ) -> None:
        self.calls.append(
            {
                "method": "look_at_world",
                "kwargs": {
                    "x": x,
                    "y": y,
                    "z": z,
                    "duration": duration,
                    "perform_movement": perform_movement,
                },
            }
        )

    def get_present_antenna_joint_positions(self) -> list[float]:
        return [0.0, 0.0]


class RuntimeSession:
    """Multi-turn v4 session around ClaudeSDKClient and ActionRuntime."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        registry: ActionRegistry,
        agent: BrainAgent,
        executor: ActionExecutor,
        speech: SpeechPresenter,
        tts: KokoroAdapter,
        actions: ActionDispatcher,
        clock: Clock | None = None,
    ) -> None:
        """Create a runtime session from prebuilt components."""
        self.config = config
        self.registry = registry
        self.agent = agent
        self.executor = executor
        self.speech = speech
        self.tts = tts
        self.actions = actions
        self.clock = clock or _SystemClock()
        self.brain = BrainProcessor(agent)
        self.bus = OutputBus()
        self._turn_workers: dict[str, set[str]] = {}
        self._started = False
        self._stopping = False
        self._surface_phase = "idle"

    @classmethod
    def from_profile(
        cls,
        profile_path: Path,
        *,
        overrides: dict[str, Any] | None = None,
        clock: Clock | None = None,
        mini_factory: Callable[[AgentConfig], Any] | None = None,
        client_factory: ClientFactory | None = None,
        cwd: Path | str | None = None,
        memory_root: Path | None = None,
    ) -> "RuntimeSession":
        """Build a runtime session from a profile directory."""
        del memory_root
        config = from_profile(profile_path, overrides=overrides)
        registry = create_builtin_registry()
        mini = mini_factory(config) if mini_factory is not None else _NoopMini()
        executor = ActionExecutor(registry=registry, mini=mini)
        bus = OutputBus()

        async def publish_result(frame: Any) -> None:
            await bus.publish(frame)

        actions = ActionDispatcher(executor, publish=publish_result)
        agent = BrainAgent(
            config=config,
            registry=registry,
            run_action=actions.run_action,
            client_factory=client_factory,
            cwd=cwd,
        )
        speech = SpeechPresenter(style={"voice": config.speech.voice, "speed": config.speech.speed})
        tts = KokoroAdapter(config.speech)
        session = cls(
            config=config,
            registry=registry,
            agent=agent,
            executor=executor,
            speech=speech,
            tts=tts,
            actions=actions,
            clock=clock,
        )
        session.bus = bus
        return session

    async def start(self) -> None:
        """Start the SDK client session."""
        if self._started:
            return
        self._started = True
        self._stopping = False
        await self.agent.start()
        await self._set_surface("idle")

    async def stop(self) -> None:
        """Cancel running actions/tasks and shut down the session."""
        if not self._started:
            return
        self._stopping = True
        self.executor.cancel()
        await self._stop_sdk_tasks()
        await self.agent.stop()
        await self.bus.close()
        self._started = False
        self._stopping = False

    def subscribe(
        self,
        *,
        filter: Callable[[Any], bool] | None = None,
    ) -> OutputSubscription:
        """Subscribe to all output frames."""
        return self.bus.subscribe(filter=filter)

    def unsubscribe(self, sub: OutputSubscription) -> None:
        """Unsubscribe from the output bus."""
        self.bus.unsubscribe(sub)

    async def submit_text(self, text: str, *, turn_id: str | None = None) -> str:
        """Submit a text turn through the BrowserInputFrame path."""
        resolved = turn_id or self._make_turn_id()
        frame = BrowserInputFrame(
            kind="text",
            payload={"text": text, "turn_id": resolved},
            session_id=resolved,
        )
        await self.submit_browser_input(frame)
        return resolved

    async def submit_browser_input(self, frame: BrowserInputFrame) -> str:
        """Submit a browser input frame and run brain processing."""
        turn_id = str(frame.payload.get("turn_id") or frame.session_id or self._make_turn_id())
        await self._set_surface("replying")
        await self._dispatch_brain(frame, turn_id=turn_id)
        return turn_id

    async def submit_audio_chunk(self, frame: AudioFrame) -> None:
        """Forward an audio frame to upstream consumers."""
        await self.bus.publish(frame)

    async def submit_speech_activity(self, frame: SpeechActivityFrame) -> None:
        """Forward speech activity to BrainProcessor, surfacing barge-in interrupts."""
        if frame.state == "start":
            await self._set_surface("listening")
        elif frame.state == "end":
            await self._set_surface("listening_wait")
        outputs = await self.brain.process(frame)
        await self._handle_processor_outputs(outputs, turn_id="")

    async def submit_transcription(self, frame: TranscriptionFrame) -> None:
        """Push a final transcription through BrainProcessor and publish previews."""
        await self.bus.publish(frame)
        if not frame.is_final:
            return
        await self._set_surface("replying")
        outputs = await self.brain.process(frame)
        await self._handle_processor_outputs(outputs, turn_id=frame.turn_id)

    async def submit_vision_event(self, frame: VisionEventFrame) -> None:
        """Push a vision event into Brain context."""
        await self.brain.process(frame)
        await self.bus.publish(frame)

    async def wait_for_turn_idle(self, turn_id: str, *, timeout: float | None = None) -> None:
        """Wait until the turn has no pending SDK task events tracked by session."""
        deadline = None
        if timeout is not None:
            deadline = self.clock.monotonic() + timeout
        while True:
            workers = self._turn_workers.get(turn_id, set())
            if not workers:
                return
            if deadline is not None and self.clock.monotonic() > deadline:
                raise asyncio.TimeoutError(f"turn {turn_id} not idle in time")
            await asyncio.sleep(0.01)

    async def _dispatch_brain(self, frame: object, *, turn_id: str) -> None:
        outputs = await self.brain.process(frame)
        await self._handle_processor_outputs(outputs, turn_id=turn_id)

    async def _handle_processor_outputs(
        self,
        outputs: list[object],
        *,
        turn_id: str,
    ) -> None:
        for item in outputs:
            if isinstance(item, SDKMessageFrame):
                await self._handle_sdk_message(item)
            elif isinstance(item, InterruptFrame):
                await self._handle_interrupt(item)
            else:
                await self.bus.publish(item)
        if turn_id:
            await self._maybe_set_idle()

    async def _handle_sdk_message(self, frame: SDKMessageFrame) -> None:
        await self.bus.publish(frame)
        worker_frame = sdk_message_to_worker_event(frame)
        if worker_frame is not None:
            await self._handle_worker_event(frame.turn_id, worker_frame)

        speech_frames = await self.speech.process(frame)
        for sf in speech_frames:
            await self.bus.publish(sf)
            if isinstance(sf, SpeechPresenterFrame):
                tts_frames = await self.tts.process(sf)
                for tf in tts_frames:
                    if isinstance(tf, TTSAudioFrame):
                        await self.brain.process(tf)
                    await self.bus.publish(tf)

    async def _handle_worker_event(
        self,
        turn_id: str,
        frame: WorkerEventFrame,
    ) -> None:
        if turn_id and frame.event in {"started", "progress"}:
            self._turn_workers.setdefault(turn_id, set()).add(frame.task_id)
        await self.brain.process(frame)
        await self.bus.publish(frame)
        if frame.event in {"completed", "failed", "stopped", "cancelled"}:
            bucket = self._turn_workers.get(turn_id)
            if bucket is not None:
                bucket.discard(frame.task_id)
                if not bucket:
                    self._turn_workers.pop(turn_id, None)

    async def _handle_interrupt(self, interrupt: InterruptFrame) -> None:
        speech_frames = await self.speech.process(interrupt)
        for sf in speech_frames:
            if isinstance(sf, TTSStopFrame):
                tts_frames = await self.tts.process(sf)
                for tf in tts_frames:
                    await self.bus.publish(tf)
            await self.bus.publish(sf)
        await self.agent.interrupt()
        cancel_actions = InterruptFrame(
            scope="actions",
            turn_id=interrupt.turn_id,
            reason=interrupt.reason or "barge_in",
            ts_ms=interrupt.ts_ms,
        )
        await self.actions.process(cancel_actions)
        await self.bus.publish(interrupt)

    async def _set_surface(self, phase: str) -> None:
        if phase == self._surface_phase:
            return
        self._surface_phase = phase
        ts_ms = int(self.clock.monotonic() * 1000)
        frame = WorkerEventFrame(
            task_id=SURFACE_TASK_ID,
            event="state",
            payload={"kind": "surface_state", "state": {"phase": phase}},
            ts_ms=ts_ms,
        )
        await self.bus.publish(frame)

    async def _maybe_set_idle(self) -> None:
        if self._stopping:
            return
        if self._turn_workers:
            return
        await self._set_surface("idle")

    async def _stop_sdk_tasks(self) -> None:
        task_ids = sorted(
            {
                task_id
                for task_ids in self._turn_workers.values()
                for task_id in task_ids
                if task_id
            }
        )
        self._turn_workers.clear()
        for task_id in task_ids:
            try:
                await self.agent.stop_task(task_id)
            except Exception:  # pragma: no cover - defensive shutdown path
                LOGGER.exception("Failed to stop SDK task %s", task_id)

    def _make_turn_id(self) -> str:
        return f"turn_{uuid.uuid4().hex[:12]}"
