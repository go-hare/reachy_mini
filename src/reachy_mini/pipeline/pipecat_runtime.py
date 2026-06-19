"""Pipecat-backed L2 runtime for Reachy Mini v4."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pipecat.frames.frames import (
    CancelFrame,
    DataFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.base_worker import WorkerParams

from .action_dispatcher import ActionDispatcher
from .brain_processor import BrainProcessor
from .frames import (
    InterruptFrame,
    SDKMessageFrame,
    SpeechPresenterFrame,
    TranscriptionFrame,
    TTSAudioFrame,
    TTSStopFrame,
)
from .output_bus import OutputBus
from .speech_presenter import SpeechPresenter
from .tts_kokoro import KokoroAdapter


@dataclass
class ReachyPipelineFrame(DataFrame):
    """Pipecat data frame carrying the v4 frozen dataclass payload."""

    payload: Any


@dataclass
class ReachyDrainFrame(DataFrame):
    """Pipecat data frame used to wait until queued v4 payloads drain."""

    future: concurrent.futures.Future[None]


PublishFrame = Callable[[Any], Awaitable[None]]


class ReachyInputSource(FrameProcessor):
    """Entry processor that injects external v4 frames into Pipecat."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward Pipecat frames downstream."""
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class ReachyBrainPipecatProcessor(FrameProcessor):
    """Pipecat processor wrapping the SDK-backed BrainProcessor."""

    def __init__(
        self,
        brain: BrainProcessor,
        *,
        timeout_s: float,
        on_timeout: Callable[[str], Awaitable[list[Any]]],
        on_error: Callable[[Exception], Awaitable[list[Any]]],
    ) -> None:
        """Create a Pipecat processor for L3 brain turns and context frames."""
        super().__init__(name="ReachyBrainPipecatProcessor")
        self._brain = brain
        self._timeout_s = timeout_s
        self._on_timeout = on_timeout
        self._on_error = on_error

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Run BrainProcessor for v4 payload frames."""
        await super().process_frame(frame, direction)
        if not isinstance(frame, ReachyPipelineFrame):
            await self.push_frame(frame, direction)
            return

        payload = frame.payload
        try:
            outputs = await asyncio.wait_for(
                self._brain.process(payload),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            outputs = await self._on_timeout(_turn_id_from_payload(payload))
        except Exception as exc:
            outputs = await self._on_error(exc)

        await self._push_payloads(outputs, direction)

    async def _push_payloads(
        self,
        payloads: list[Any],
        direction: FrameDirection,
    ) -> None:
        for payload in payloads:
            await self.push_frame(ReachyPipelineFrame(payload=payload), direction)


class ReachySpeechPipecatProcessor(FrameProcessor):
    """Pipecat processor wrapping speech presentation and TTS adapters."""

    def __init__(
        self,
        *,
        brain: BrainProcessor,
        speech: SpeechPresenter,
        tts: KokoroAdapter,
    ) -> None:
        """Create a speech processor for SDK messages and interrupts."""
        super().__init__(name="ReachySpeechPipecatProcessor")
        self._brain = brain
        self._speech = speech
        self._tts = tts

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Convert SDK text and interrupts to speech/TTS frames."""
        await super().process_frame(frame, direction)
        if not isinstance(frame, ReachyPipelineFrame):
            await self.push_frame(frame, direction)
            return

        payload = frame.payload
        outputs: list[Any] = [payload]
        if isinstance(payload, SDKMessageFrame):
            outputs.extend(await self._speech_from_sdk(payload))
        elif isinstance(payload, InterruptFrame):
            outputs.extend(await self._speech_from_interrupt(payload))

        for output in outputs:
            await self.push_frame(ReachyPipelineFrame(payload=output), direction)

    async def _speech_from_sdk(self, frame: SDKMessageFrame) -> list[Any]:
        outputs: list[Any] = []
        speech_frames = await self._speech.process(frame)
        for speech_frame in speech_frames:
            outputs.append(speech_frame)
            if isinstance(speech_frame, SpeechPresenterFrame):
                tts_frames = await self._tts.process(speech_frame)
                for tts_frame in tts_frames:
                    if isinstance(tts_frame, TTSAudioFrame):
                        await self._brain.process(tts_frame)
                    outputs.append(tts_frame)
        return outputs

    async def _speech_from_interrupt(self, frame: InterruptFrame) -> list[Any]:
        outputs: list[Any] = []
        speech_frames = await self._speech.process(frame)
        for speech_frame in speech_frames:
            if isinstance(speech_frame, TTSStopFrame):
                outputs.extend(await self._tts.process(speech_frame))
            outputs.append(speech_frame)
        return outputs


class ReachyActionPipecatProcessor(FrameProcessor):
    """Pipecat processor handling runtime interrupts."""

    def __init__(
        self,
        on_interrupt: Callable[[InterruptFrame], Awaitable[None]],
    ) -> None:
        """Create an interrupt bridge for SDK and L1 control."""
        super().__init__(name="ReachyActionPipecatProcessor")
        self._on_interrupt = on_interrupt

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward payloads and let ActionDispatcher observe interrupts."""
        await super().process_frame(frame, direction)
        if not isinstance(frame, ReachyPipelineFrame):
            await self.push_frame(frame, direction)
            return
        if isinstance(frame.payload, InterruptFrame):
            await self._on_interrupt(frame.payload)
        await self.push_frame(frame, direction)


class ReachyOutputCollector(FrameProcessor):
    """Final Pipecat processor publishing v4 payloads to OutputBus."""

    def __init__(
        self,
        publish: PublishFrame,
        *,
        on_sdk_message: Callable[[SDKMessageFrame], Awaitable[list[Any]]],
    ) -> None:
        """Create a collector that turns Pipecat output into bus frames."""
        super().__init__(name="ReachyOutputCollector")
        self._publish = publish
        self._on_sdk_message = on_sdk_message

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Publish v4 payloads while preserving Pipecat frame flow."""
        await super().process_frame(frame, direction)
        if isinstance(frame, ReachyDrainFrame):
            if not frame.future.done():
                frame.future.set_result(None)
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, ReachyPipelineFrame):
            await self._publish_payload(frame.payload)
        await self.push_frame(frame, direction)

    async def _publish_payload(self, payload: Any) -> None:
        if isinstance(payload, SDKMessageFrame):
            extra = await self._on_sdk_message(payload)
            await self._publish(payload)
            for item in extra:
                await self._publish(item)
            return
        await self._publish(payload)


class ReachyPipecatRuntime:
    """Lifecycle shell for the real Pipecat L2 graph."""

    def __init__(
        self,
        *,
        bus: OutputBus,
        brain: BrainProcessor,
        speech: SpeechPresenter,
        tts: KokoroAdapter,
        actions: ActionDispatcher,
        on_interrupt: Callable[[InterruptFrame], Awaitable[None]],
        on_sdk_message: Callable[[SDKMessageFrame], Awaitable[list[Any]]],
        on_brain_timeout: Callable[[str], Awaitable[list[Any]]],
        on_brain_error: Callable[[Exception], Awaitable[list[Any]]],
        brain_turn_timeout_s: float,
        audio_in_sample_rate: int,
        audio_out_sample_rate: int,
    ) -> None:
        """Build the Pipecat worker graph but do not start it."""
        self.bus = bus
        self.brain = brain
        self.speech = speech
        self.tts = tts
        self.actions = actions
        self.input = ReachyInputSource(name="ReachyInputSource")
        self._run_future: concurrent.futures.Future[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready: concurrent.futures.Future[None] | None = None
        pipeline = Pipeline(
            [
                self.input,
                ReachyBrainPipecatProcessor(
                    brain,
                    timeout_s=brain_turn_timeout_s,
                    on_timeout=on_brain_timeout,
                    on_error=on_brain_error,
                ),
                ReachySpeechPipecatProcessor(
                    brain=brain,
                    speech=speech,
                    tts=tts,
                ),
                ReachyActionPipecatProcessor(on_interrupt),
                ReachyOutputCollector(
                    bus.publish,
                    on_sdk_message=on_sdk_message,
                ),
            ]
        )
        self.pipeline = pipeline
        self.worker = PipelineWorker(
            pipeline,
            enable_rtvi=False,
            enable_turn_tracking=False,
            idle_timeout_secs=None,
            params=PipelineParams(
                audio_in_sample_rate=audio_in_sample_rate,
                audio_out_sample_rate=audio_out_sample_rate,
                enable_heartbeats=False,
            ),
            name="reachy-v4-pipeline",
        )

    async def start(self) -> None:
        """Start the Pipecat worker in the background."""
        if self._thread is not None:
            return
        ready: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._ready = ready
        thread = threading.Thread(
            target=self._run_worker_thread,
            args=(ready,),
            daemon=True,
            name="reachy-v4-pipecat",
        )
        self._thread = thread
        thread.start()
        await asyncio.wrap_future(ready)

    async def stop(self) -> None:
        """Cancel the Pipecat worker graph and wait for cleanup."""
        loop = self._loop
        future = self._run_future
        thread = self._thread
        self._run_future = None
        self._loop = None
        self._thread = None
        self._ready = None
        if loop is not None and future is not None:
            with contextlib.suppress(Exception):
                await self._run_on_loop(self.worker.cancel(reason="runtime_stop"), loop)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.wrap_future(future), timeout=3.0)
            if not future.done():
                future.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wrap_future(future)
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            await asyncio.to_thread(thread.join, 2.0)

    async def submit(self, payload: Any, *, wait: bool = True) -> None:
        """Submit one v4 payload into the Pipecat graph."""
        await self._run_on_worker_loop(
            self.worker.queue_frame(ReachyPipelineFrame(payload=payload))
        )
        if not wait:
            return
        future: concurrent.futures.Future[None] = concurrent.futures.Future()
        await self._run_on_worker_loop(
            self.worker.queue_frame(ReachyDrainFrame(future=future))
        )
        await asyncio.wrap_future(future)

    async def interrupt(self) -> None:
        """Broadcast a Pipecat interruption system frame."""
        await self._run_on_worker_loop(self.worker.queue_frame(InterruptionFrame()))

    async def end_when_done(self) -> None:
        """Ask the worker to finish after queued frames drain."""
        await self._run_on_worker_loop(self.worker.queue_frame(EndFrame()))

    async def cancel(self, reason: str = "") -> None:
        """Cancel the worker through Pipecat."""
        await self._run_on_worker_loop(self.worker.queue_frame(CancelFrame(reason=reason)))

    async def _run_on_worker_loop(self, coro: Any) -> Any:
        loop = self._loop
        running_loop = asyncio.get_running_loop()
        if loop is None or loop is running_loop:
            return await coro
        return await self._run_on_loop(coro, loop)

    async def _run_on_loop(self, coro: Any, loop: asyncio.AbstractEventLoop) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return await asyncio.wrap_future(future)
        except Exception:
            if not future.done():
                future.cancel()
            raise

    def _run_worker_thread(self, ready: concurrent.futures.Future[None]) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            run_future = asyncio.run_coroutine_threadsafe(
                self._run_worker_until_ready(ready),
                loop,
            )
            self._run_future = run_future
            loop.run_forever()
            if not run_future.done():
                run_future.cancel()
            with contextlib.suppress(Exception):
                run_future.result(timeout=2.0)
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    async def _run_worker_until_ready(
        self,
        ready: concurrent.futures.Future[None],
    ) -> None:
        @self.worker.event_handler("on_pipeline_started")
        async def _mark_ready(_worker: Any, _frame: Any) -> None:
            if not ready.done():
                ready.set_result(None)

        params = WorkerParams(loop=asyncio.get_running_loop())
        await self.worker.run(params)


def _turn_id_from_payload(payload: Any) -> str:
    if isinstance(payload, TranscriptionFrame):
        return payload.turn_id
    if hasattr(payload, "payload"):
        inner = getattr(payload, "payload")
        if isinstance(inner, dict):
            return str(inner.get("turn_id") or "")
    return ""


__all__ = [
    "ReachyActionPipecatProcessor",
    "ReachyBrainPipecatProcessor",
    "ReachyDrainFrame",
    "ReachyInputSource",
    "ReachyOutputCollector",
    "ReachyPipecatRuntime",
    "ReachyPipelineFrame",
    "ReachySpeechPipecatProcessor",
]
