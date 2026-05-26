"""Long-lived v4 runtime session that wires Brain, ActionRuntime and Pipecat-style frames."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from reachy_mini.action_runtime import ActionExecutor, ActionResult, ActionSpec
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.action_runtime.registry import ActionRegistry
from reachy_mini.reachy_brain.agent import BrainAgent
from reachy_mini.reachy_brain.config import AgentConfig, from_profile
from reachy_mini.reachy_brain.coordinator import WorkerCoordinator
from reachy_mini.reachy_brain.worker import WorkerEvent

from .action_dispatcher import ActionDispatcher
from .brain_processor import BrainProcessor
from .frames import (
    AudioFrame,
    BrainReplyFrame,
    BrowserInputFrame,
    InterruptFrame,
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
        ...


class _SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()


class _NoopMini:
    """Fallback SDK object used when no factory is supplied (text/mock mode)."""

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
    """Multi-turn v4 session wiring Brain, ActionRuntime, SpeechPresenter and TTS.

    Responsibilities:
      - Build all components from a profile.
      - Accept input frames (text/audio/speech-activity/vision/transcription).
      - Run BrainProcessor for completed turns and publish frames to the bus.
      - Dispatch ActionSpecs through ActionExecutor in the background.
      - Spawn long-running workers via WorkerCoordinator and surface their events.
      - Route barge-in (SpeechActivityFrame -> InterruptFrame) to speech and actions.
    """

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
        coordinator: WorkerCoordinator,
        clock: Clock | None = None,
        memory_root: Path | None = None,
    ) -> None:
        """Create a runtime session from prebuilt components."""
        self.config = config
        self.registry = registry
        self.agent = agent
        self.executor = executor
        self.speech = speech
        self.tts = tts
        self.actions = actions
        self.coordinator = coordinator
        self.clock = clock or _SystemClock()
        self.memory_root = memory_root
        self.brain = BrainProcessor(agent)
        self.bus = OutputBus()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._action_tasks: dict[str, asyncio.Task[Any]] = {}
        self._turn_actions: dict[str, set[str]] = {}
        self._turn_workers: dict[str, set[str]] = {}
        self._worker_to_turn: dict[str, str] = {}
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
        memory_root: Path | None = None,
    ) -> "RuntimeSession":
        """Build a runtime session from a profile directory."""
        config = from_profile(profile_path, overrides=overrides)
        registry = create_builtin_registry()
        mini = mini_factory(config) if mini_factory is not None else _NoopMini()
        agent = BrainAgent(config=config, registry=registry)
        executor = ActionExecutor(registry=registry, mini=mini)
        speech = SpeechPresenter()
        tts = KokoroAdapter(config.speech)
        actions = ActionDispatcher(executor)

        async def emit_action(spec: ActionSpec) -> ActionResult:
            return await executor.submit(spec)

        coordinator = WorkerCoordinator(
            emit_action=emit_action,
            memory_root=memory_root,
        )
        return cls(
            config=config,
            registry=registry,
            agent=agent,
            executor=executor,
            speech=speech,
            tts=tts,
            actions=actions,
            coordinator=coordinator,
            clock=clock,
            memory_root=memory_root,
        )

    async def start(self) -> None:
        """Start session-scoped background tasks."""
        if self._started:
            return
        self._started = True
        self._stopping = False
        self.coordinator.event_sink = self._on_worker_event
        await self._set_surface("idle")

    async def stop(self) -> None:
        """Cancel running actions and workers and shut the bus down."""
        if not self._started:
            return
        self._stopping = True
        self.coordinator.cancel_all()
        self.executor.cancel()
        action_tasks = list(self._action_tasks.values())
        for task in action_tasks:
            task.cancel()
        for task in list(self._tasks):
            task.cancel()
        with contextlib.suppress(asyncio.TimeoutError):
            await self.coordinator.wait_all(timeout=2.0)
        if action_tasks:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(
                    asyncio.gather(*action_tasks, return_exceptions=True),
                    timeout=2.0,
                )
        if self._tasks:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(
                    asyncio.gather(*list(self._tasks), return_exceptions=True),
                    timeout=2.0,
                )
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
        """Submit a browser input frame and schedule brain processing."""
        turn_id = str(frame.payload.get("turn_id") or frame.session_id or self._make_turn_id())
        await self._set_surface("replying")
        await self._dispatch_brain(frame, turn_id=turn_id)
        return turn_id

    async def submit_audio_chunk(self, frame: AudioFrame) -> None:
        """Forward an audio frame to upstream consumers; STT is built per-deployment."""
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
        """Wait until the turn has no pending actions or workers."""
        deadline = None
        if timeout is not None:
            deadline = self.clock.monotonic() + timeout
        while True:
            actions = self._turn_actions.get(turn_id, set())
            workers = self._turn_workers.get(turn_id, set())
            if not actions and not workers:
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
            if isinstance(item, BrainReplyFrame):
                await self._handle_brain_reply(item)
            elif isinstance(item, InterruptFrame):
                await self._handle_interrupt(item)
            else:
                await self.bus.publish(item)
        if not outputs and turn_id and turn_id not in self._turn_actions:
            await self._maybe_set_idle()

    async def _handle_brain_reply(self, reply: BrainReplyFrame) -> None:
        await self.bus.publish(reply)
        speech_frames = await self.speech.process(reply)
        for sf in speech_frames:
            await self.bus.publish(sf)
            if isinstance(sf, SpeechPresenterFrame):
                tts_frames = await self.tts.process(sf)
                for tf in tts_frames:
                    if isinstance(tf, TTSAudioFrame):
                        await self.brain.process(tf)
                    await self.bus.publish(tf)
        if reply.actions:
            await self._set_surface("acting")
            for spec in reply.actions:
                await self._spawn_action(spec, turn_id=reply.turn_id)
        if reply.worker_decision:
            task_id = await self.coordinator.handle_decision(
                reply.worker_decision,
                parent_request_id=reply.request_id,
            )
            if task_id:
                self._turn_workers.setdefault(reply.turn_id, set()).add(task_id)
                self._worker_to_turn[task_id] = reply.turn_id
        if not reply.actions and not reply.worker_decision:
            await self._maybe_set_idle()

    async def _spawn_action(self, spec: ActionSpec, *, turn_id: str) -> None:
        request_id = spec.request_id or self._make_request_id()
        if not spec.request_id:
            spec = ActionSpec(
                name=spec.name,
                params=spec.params,
                reason=spec.reason,
                request_id=request_id,
                owner_id=spec.owner_id,
                priority=spec.priority,
                interruptible=spec.interruptible,
                deadline_s=spec.deadline_s,
                parent_request_id=spec.parent_request_id,
            )
        self._turn_actions.setdefault(turn_id, set()).add(request_id)
        task = asyncio.create_task(self._run_action(spec, turn_id=turn_id, request_id=request_id))
        self._action_tasks[request_id] = task

    async def _run_action(
        self,
        spec: ActionSpec,
        *,
        turn_id: str,
        request_id: str,
    ) -> None:
        from .frames import ActionResultFrame, ActionSpecFrame

        await self.bus.publish(ActionSpecFrame(spec=spec, turn_id=turn_id))
        try:
            result = await self.executor.submit(spec)
        except asyncio.CancelledError:
            self._action_tasks.pop(request_id, None)
            self._discard_action(turn_id, request_id)
            raise
        except Exception as exc:
            await self.bus.publish(
                ActionResultFrame(
                    request_id=request_id,
                    name=spec.name,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    duration_ms=0,
                )
            )
            self._action_tasks.pop(request_id, None)
            self._discard_action(turn_id, request_id)
            await self._maybe_set_idle()
            return
        await self.bus.publish(ActionResultFrame.from_result(result))
        self._action_tasks.pop(request_id, None)
        self._discard_action(turn_id, request_id)
        await self._maybe_set_idle()

    def _discard_action(self, turn_id: str, request_id: str) -> None:
        bucket = self._turn_actions.get(turn_id)
        if bucket is None:
            return
        bucket.discard(request_id)
        if not bucket:
            self._turn_actions.pop(turn_id, None)

    async def _handle_interrupt(self, interrupt: InterruptFrame) -> None:
        speech_frames = await self.speech.process(interrupt)
        for sf in speech_frames:
            if isinstance(sf, TTSStopFrame):
                tts_frames = await self.tts.process(sf)
                for tf in tts_frames:
                    await self.bus.publish(tf)
            await self.bus.publish(sf)
        cancel_actions = InterruptFrame(
            scope="actions",
            turn_id=interrupt.turn_id,
            reason=interrupt.reason or "barge_in",
            ts_ms=interrupt.ts_ms,
        )
        await self.actions.process(cancel_actions)
        await self.bus.publish(interrupt)

    async def _on_worker_event(self, event: WorkerEvent) -> None:
        frame = WorkerEventFrame(
            task_id=event.task_id,
            event=event.event,
            payload=dict(event.payload),
            ts_ms=event.ts_ms,
        )
        await self.brain.process(frame)
        await self.bus.publish(frame)
        if event.event in {"completed", "failed", "cancelled"}:
            turn_id = self._worker_to_turn.pop(event.task_id, "")
            bucket = self._turn_workers.get(turn_id)
            if bucket is not None:
                bucket.discard(event.task_id)
                if not bucket:
                    self._turn_workers.pop(turn_id, None)
            await self._maybe_set_idle()

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
        if self._action_tasks or self._turn_actions:
            return
        if self._turn_workers:
            return
        await self._set_surface("idle")

    def _make_turn_id(self) -> str:
        return f"turn_{uuid.uuid4().hex[:12]}"

    def _make_request_id(self) -> str:
        return f"req_{uuid.uuid4().hex[:12]}"
