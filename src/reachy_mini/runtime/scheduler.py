"""Single-brain runtime scheduler backed by one resident ccmini agent."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ccmini.messages import (
    CompletionEvent,
    ErrorEvent,
    PendingToolCallEvent,
    StreamEvent,
    ThinkingEvent,
    ToolProgressEvent,
    ToolUseSummaryEvent,
    TextEvent,
)
from reachy_mini.affect import (
    AffectRuntime,
    AffectState,
    EmotionSignal,
    create_affect_runtime,
)
from reachy_mini.companion import (
    build_companion_phase_surface_state,
    build_idle_surface_state,
    build_listening_surface_state,
    build_listening_wait_surface_state,
)
from reachy_mini.runtime.agent_bridge import build_runtime_agent_bundle
from reachy_mini.runtime.tools import ReachyToolContext

if TYPE_CHECKING:
    from ccmini.agent import Agent
    from reachy_mini.runtime.config import ProfileRuntimeConfig
    from reachy_mini.runtime.profile_loader import ProfileBundle


LOGGER = logging.getLogger(__name__)
_DEFAULT_SPEECH_INTERRUPT_GRACE_S = 0.5


def _default_affect_model_path() -> Path:
    """Resolve the bundled Chordia model directory."""

    return Path(__file__).resolve().parents[1] / "mode" / "Chordia"


@dataclass(slots=True, frozen=True)
class FrontOutputPacket:
    """One browser-visible event emitted by the runtime."""

    type: str
    thread_id: str
    turn_id: str
    text: str = ""
    error: str = ""
    payload: dict[str, Any] | None = None

    def as_event(self) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": self.type,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
        }
        if self.type in {"speech_preview", "text_delta", "turn_done", "thinking"}:
            event["text"] = self.text
        elif self.type == "tool_progress":
            event["content"] = self.text
        elif self.type == "turn_error":
            event["error"] = self.error
        if self.payload:
            for key, value in self.payload.items():
                if key not in event:
                    event[key] = value
        return event


class RuntimeScheduler:
    """Resident runtime host that routes turns into one ccmini agent."""

    @classmethod
    def from_profile(
        cls,
        *,
        profile: "ProfileBundle",
        config: "ProfileRuntimeConfig",
        enable_affect: bool = True,
        runtime_tool_context: ReachyToolContext | None = None,
    ) -> "RuntimeScheduler":
        runtime_bundle = build_runtime_agent_bundle(
            profile=profile,
            config=config,
            runtime_context=runtime_tool_context,
        )
        affect_runtime = None
        if enable_affect:
            affect_runtime = create_affect_runtime(profile.root, _default_affect_model_path())
        return cls(
            profile_root=profile.root,
            agent=runtime_bundle.agent,
            affect_runtime=affect_runtime,
            runtime_tool_context=runtime_tool_context,
        )

    def __init__(
        self,
        *,
        profile_root: Path,
        agent: "Agent",
        affect_runtime: AffectRuntime | None = None,
        speech_interrupt_grace_s: float = _DEFAULT_SPEECH_INTERRUPT_GRACE_S,
        runtime_tool_context: ReachyToolContext | None = None,
    ) -> None:
        self.profile_root = profile_root
        self.agent = agent
        self.affect_runtime = affect_runtime
        self.runtime_tool_context = runtime_tool_context
        self._speech_interrupt_grace_s = max(float(speech_interrupt_grace_s), 0.0)
        self._lifecycle_lock = asyncio.Lock()
        self._listener_task: asyncio.Task[None] | None = None
        self._listener_error: BaseException | None = None
        self._event_queue: asyncio.Queue[StreamEvent] | None = None
        self._agent_unsubscribe: Callable[[], None] | None = None
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._front_output_subscribers: set[asyncio.Queue[FrontOutputPacket]] = set()
        self._thread_surface_state: dict[str, dict[str, Any]] = {}
        self._thread_current_turn_id: dict[str, str] = {}
        self._thread_turn_futures: dict[str, asyncio.Future[None]] = {}
        self._thread_final_text: dict[str, str] = {}
        self._thread_surface_handlers: dict[
            str, Callable[[dict[str, Any]], Awaitable[None] | None] | None
        ] = {}
        self._thread_final_reply_handlers: dict[
            str, Callable[[dict[str, Any]], Awaitable[bool] | bool | None] | None
        ] = {}
        self._turn_thread_ids: dict[str, str] = {}
        self._thread_user_speaking: dict[str, bool] = {}
        self._thread_affect_state: dict[str, AffectState | None] = {}
        self._thread_emotion_signal: dict[str, EmotionSignal | None] = {}
        self._thread_reply_audio_interrupted: set[str] = set()
        self._pending_reply_audio_interrupt_tasks: dict[str, asyncio.Task[None]] = {}
        self._active_thread_id: str = ""

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._listener_task is not None and not self._listener_task.done():
                return

            self._listener_error = None
            self._runtime_loop = asyncio.get_running_loop()
            self._event_queue = asyncio.Queue()
            await self.agent.start()

            def _on_event(event: StreamEvent) -> None:
                loop = self._runtime_loop
                queue = self._event_queue
                if loop is None or queue is None:
                    return
                loop.call_soon_threadsafe(queue.put_nowait, event)

            self._agent_unsubscribe = self.agent.on_event(_on_event)
            self._listener_task = asyncio.create_task(self._listen_agent_events())

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            listener = self._listener_task
            self._listener_task = None
            if listener is not None:
                listener.cancel()
                try:
                    await listener
                except asyncio.CancelledError:
                    pass

            unsubscribe = self._agent_unsubscribe
            self._agent_unsubscribe = None
            if unsubscribe is not None:
                unsubscribe()

            for task in list(self._pending_reply_audio_interrupt_tasks.values()):
                task.cancel()
            self._pending_reply_audio_interrupt_tasks.clear()

            for future in list(self._thread_turn_futures.values()):
                if not future.done():
                    future.set_result(None)
            self._thread_turn_futures.clear()
            self._thread_current_turn_id.clear()
            self._thread_final_text.clear()
            self._thread_surface_handlers.clear()
            self._thread_final_reply_handlers.clear()
            self._turn_thread_ids.clear()
            self._thread_surface_state.clear()
            self._thread_user_speaking.clear()
            self._thread_affect_state.clear()
            self._thread_emotion_signal.clear()
            self._thread_reply_audio_interrupted.clear()
            self._active_thread_id = ""
            self._event_queue = None
            self._runtime_loop = None
            await self.agent.stop()

    async def handle_user_turn(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        final_reply_handler: Callable[[dict[str, Any]], Awaitable[bool] | bool | None]
        | None = None,
    ) -> str:
        """Submit one user turn into the single-brain runtime."""

        _ = final_reply_handler
        self._ensure_running()
        self._raise_if_listener_failed()
        self._cancel_reply_audio_interrupt_task(thread_id)
        self._publish_speech_preview(thread_id=thread_id, turn_id="", text="")
        self._thread_surface_handlers[thread_id] = surface_state_handler
        self._thread_final_reply_handlers[thread_id] = final_reply_handler

        previous_active_thread = self._active_thread_id
        previous_turn_id = self._thread_current_turn_id.get(previous_active_thread, "")
        if previous_turn_id:
            self._mark_turn_stale(previous_active_thread, previous_turn_id)

        affect_turn = self._evolve_affect_turn(user_text)
        affect_state = affect_turn.state if affect_turn is not None else None
        emotion_signal = affect_turn.emotion_signal if affect_turn is not None else None
        self._thread_affect_state[thread_id] = affect_state
        self._thread_emotion_signal[thread_id] = emotion_signal

        await self._push_surface_state(
            thread_id=thread_id,
            state=build_companion_phase_surface_state(
                thread_id=thread_id,
                phase="replying",
                affect_state=affect_state,
                emotion_signal=emotion_signal,
            ),
            surface_state_handler=surface_state_handler,
        )

        turn_future = asyncio.get_running_loop().create_future()
        self._thread_turn_futures[thread_id] = turn_future
        self._thread_final_text[thread_id] = ""

        try:
            turn_id = await self._submit_with_preemption(
                text=str(user_text or ""),
                conversation_id=thread_id,
                user_id=user_id,
                metadata={
                    "thread_id": thread_id,
                    "session_id": str(session_id or ""),
                    "source": "runtime",
                },
            )
        except Exception as exc:
            if not turn_future.done():
                turn_future.set_result(None)
            self._thread_turn_futures.pop(thread_id, None)
            self._thread_surface_handlers.pop(thread_id, None)
            self._thread_final_reply_handlers.pop(thread_id, None)
            await self._publish_turn_error(
                thread_id=thread_id,
                turn_id="",
                error=str(exc),
            )
            await self._push_surface_state(
                thread_id=thread_id,
                state=build_idle_surface_state(
                    thread_id=thread_id,
                    affect_state=affect_state,
                    emotion_signal=emotion_signal,
                ),
                surface_state_handler=surface_state_handler,
            )
            raise

        self._thread_current_turn_id[thread_id] = turn_id
        self._turn_thread_ids[turn_id] = thread_id
        self._active_thread_id = thread_id
        return turn_id

    async def wait_for_thread_idle(self, thread_id: str, timeout: float = 600.0) -> None:
        """Wait until the current visible turn of one thread completes."""

        future = self._thread_turn_futures.get(thread_id)
        if future is None or future.done():
            return
        await asyncio.wait_for(asyncio.shield(future), timeout=timeout)

    async def handle_user_speech_started(
        self,
        *,
        thread_id: str,
        user_text: str = "",
        metadata: dict[str, Any] | None = None,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        """Switch the host into listening state and interrupt reply audio when needed."""

        _ = user_text, metadata
        self._ensure_running()
        self._thread_user_speaking[thread_id] = True
        self._schedule_reply_audio_interrupt(thread_id)
        await self._push_surface_state(
            thread_id=thread_id,
            state=build_listening_surface_state(
                thread_id=thread_id,
                affect_state=self._thread_affect_state.get(thread_id),
                emotion_signal=self._thread_emotion_signal.get(thread_id),
            ),
            surface_state_handler=surface_state_handler,
        )

    async def handle_user_speech_partial(
        self,
        *,
        thread_id: str,
        user_text: str,
        metadata: dict[str, Any] | None = None,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        """Forward streaming speech preview text to the browser."""

        _ = metadata, surface_state_handler
        self._ensure_running()
        self._publish_speech_preview(
            thread_id=thread_id,
            turn_id="",
            text=str(user_text or ""),
        )

    async def handle_user_speech_stopped(
        self,
        *,
        thread_id: str,
        user_text: str = "",
        metadata: dict[str, Any] | None = None,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        """Switch the host into listening-wait state after user speech ends."""

        _ = user_text, metadata
        self._ensure_running()
        self._thread_user_speaking[thread_id] = False
        self._cancel_reply_audio_interrupt_task(thread_id)
        await self._push_surface_state(
            thread_id=thread_id,
            state=build_listening_wait_surface_state(
                thread_id=thread_id,
                affect_state=self._thread_affect_state.get(thread_id),
                emotion_signal=self._thread_emotion_signal.get(thread_id),
            ),
            surface_state_handler=surface_state_handler,
        )

    def get_thread_surface_state(self, thread_id: str) -> dict[str, Any] | None:
        """Return the latest host surface state for one thread."""

        state = self._thread_surface_state.get(thread_id)
        return dict(state) if state is not None else None

    def subscribe_front_outputs(self) -> asyncio.Queue[FrontOutputPacket]:
        """Subscribe to browser-visible runtime events."""

        queue: asyncio.Queue[FrontOutputPacket] = asyncio.Queue()
        self._front_output_subscribers.add(queue)
        return queue

    def unsubscribe_front_outputs(self, queue: asyncio.Queue[FrontOutputPacket]) -> None:
        """Remove one event subscriber."""

        self._front_output_subscribers.discard(queue)

    async def _listen_agent_events(self) -> None:
        queue = self._event_queue
        if queue is None:
            return
        try:
            while True:
                event = await queue.get()
                try:
                    await self._handle_agent_event(event)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._listener_error = exc
            raise

    async def _handle_agent_event(self, event: StreamEvent) -> None:
        thread_id = self._resolve_thread_id(event)
        if not thread_id:
            return

        turn_id = str(getattr(event, "turn_id", "") or "")
        if turn_id and turn_id != self._thread_current_turn_id.get(thread_id, ""):
            return

        payload = self._resolve_event_payload(event)
        if isinstance(event, TextEvent):
            self._thread_final_text[thread_id] = (
                self._thread_final_text.get(thread_id, "") + str(event.text or "")
            )
            self._publish_front_output(
                FrontOutputPacket(
                    type="text_delta",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    text=str(event.text or ""),
                    payload=payload,
                )
            )
            return

        if isinstance(event, ThinkingEvent):
            self._publish_front_output(
                FrontOutputPacket(
                    type="thinking",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    text=str(event.text or ""),
                    payload=payload,
                )
            )
            return

        if isinstance(event, ToolProgressEvent):
            self._publish_front_output(
                FrontOutputPacket(
                    type="tool_progress",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    text=str(event.content or ""),
                    payload=payload,
                )
            )
            return

        if isinstance(event, ToolUseSummaryEvent):
            self._publish_front_output(
                FrontOutputPacket(
                    type="thinking",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    text=str(event.summary or ""),
                    payload=payload,
                )
            )
            return

        if isinstance(event, PendingToolCallEvent):
            await self._publish_turn_error(
                thread_id=thread_id,
                turn_id=turn_id,
                error="runtime does not support host-side pending tool calls yet",
            )
            await self._finalize_turn(
                thread_id=thread_id,
                turn_id=turn_id,
                surface_state_handler=self._thread_surface_handlers.get(thread_id),
            )
            return

        if isinstance(event, CompletionEvent):
            text = str(event.text or "").strip() or self._thread_final_text.get(thread_id, "")
            self._publish_front_output(
                FrontOutputPacket(
                    type="turn_done",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    text=text,
                    payload=payload,
                )
            )
            await self._invoke_final_reply_handler(
                handler=self._thread_final_reply_handlers.get(thread_id),
                thread_id=thread_id,
                turn_id=turn_id,
                user_text="",
                text=text,
            )
            await self._finalize_turn(
                thread_id=thread_id,
                turn_id=turn_id,
                surface_state_handler=self._thread_surface_handlers.get(thread_id),
            )
            return

        if isinstance(event, ErrorEvent):
            await self._publish_turn_error(
                thread_id=thread_id,
                turn_id=turn_id,
                error=str(event.error or ""),
            )
            await self._finalize_turn(
                thread_id=thread_id,
                turn_id=turn_id,
                surface_state_handler=self._thread_surface_handlers.get(thread_id),
            )

    async def _finalize_turn(
        self,
        *,
        thread_id: str,
        turn_id: str,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        affect_state = self._thread_affect_state.get(thread_id)
        emotion_signal = self._thread_emotion_signal.get(thread_id)
        interrupted = self._consume_reply_audio_interrupted(thread_id)
        user_speaking = self._thread_user_speaking.get(thread_id, False)
        if not interrupted and not user_speaking:
            await self._push_surface_state(
                thread_id=thread_id,
                state=build_companion_phase_surface_state(
                    thread_id=thread_id,
                    phase="settling",
                    affect_state=affect_state,
                    emotion_signal=emotion_signal,
                ),
                surface_state_handler=surface_state_handler,
            )
            await self._push_surface_state(
                thread_id=thread_id,
                state=build_idle_surface_state(
                    thread_id=thread_id,
                    affect_state=affect_state,
                    emotion_signal=emotion_signal,
                ),
                surface_state_handler=surface_state_handler,
            )
        self._complete_turn(thread_id, turn_id)

    async def _push_surface_state(
        self,
        *,
        thread_id: str,
        state: dict[str, Any],
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        self._thread_surface_state[thread_id] = dict(state)
        if surface_state_handler is not None:
            maybe_awaitable = surface_state_handler(dict(state))
            if isawaitable(maybe_awaitable):
                await maybe_awaitable

    async def _publish_turn_error(self, *, thread_id: str, turn_id: str, error: str) -> None:
        error_text = str(error or "").strip()
        if not error_text:
            return
        self._publish_front_output(
            FrontOutputPacket(
                type="turn_error",
                thread_id=thread_id,
                turn_id=turn_id,
                error=error_text,
            )
        )

    async def _submit_with_preemption(
        self,
        *,
        text: str,
        conversation_id: str,
        user_id: str,
        metadata: dict[str, Any],
    ) -> str:
        if self.agent.cancel_submit():
            await asyncio.sleep(0)
        last_error: RuntimeError | None = None
        for _ in range(5):
            try:
                return self.agent.submit_user_input(
                    text,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    metadata=metadata,
                )
            except RuntimeError as exc:
                if "already in progress" not in str(exc):
                    raise
                last_error = exc
                self.agent.cancel_submit()
                await asyncio.sleep(0.05)
        if last_error is not None:
            raise last_error
        raise RuntimeError("failed to submit user input")

    def _publish_front_output(self, packet: FrontOutputPacket) -> None:
        for subscriber in list(self._front_output_subscribers):
            subscriber.put_nowait(packet)

    def _publish_speech_preview(self, *, thread_id: str, turn_id: str, text: str) -> None:
        self._publish_front_output(
            FrontOutputPacket(
                type="speech_preview",
                thread_id=thread_id,
                turn_id=turn_id,
                text=str(text or ""),
            )
        )

    def _resolve_thread_id(self, event: StreamEvent) -> str:
        conversation_id = str(getattr(event, "conversation_id", "") or "").strip()
        if conversation_id:
            return conversation_id
        metadata = getattr(event, "metadata", None)
        if isinstance(metadata, dict):
            return str(metadata.get("thread_id", "") or "").strip()
        return ""

    def _resolve_event_payload(self, event: StreamEvent) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in ("conversation_id", "run_id", "tool_use_id"):
            value = str(getattr(event, key, "") or "").strip()
            if value:
                payload[key] = value
        metadata = getattr(event, "metadata", None)
        if isinstance(event, ThinkingEvent):
            payload.update(
                {
                    "phase": event.phase,
                    "source": event.source,
                    "is_redacted": bool(event.is_redacted),
                }
            )
        if isinstance(event, ToolProgressEvent):
            payload.update(
                {
                    "tool_name": event.tool_name,
                }
            )
        if isinstance(metadata, dict):
            filtered = {
                key: value
                for key, value in metadata.items()
                if key not in {"surface_state_handler", "final_reply_handler", "thread_id", "session_id"}
            }
            if filtered:
                payload["metadata"] = filtered
        return payload

    async def _invoke_final_reply_handler(
        self,
        *,
        handler: Callable[[dict[str, Any]], Awaitable[bool] | bool | None] | None,
        thread_id: str,
        turn_id: str,
        user_text: str,
        text: str,
    ) -> None:
        if handler is None:
            return
        payload = {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "user_text": user_text,
            "text": str(text or "").strip(),
        }
        maybe_awaitable = handler(payload)
        if isawaitable(maybe_awaitable):
            await maybe_awaitable

    def _mark_turn_stale(self, thread_id: str, turn_id: str) -> None:
        if self._thread_current_turn_id.get(thread_id, "") == turn_id:
            self._thread_current_turn_id[thread_id] = ""
        future = self._thread_turn_futures.get(thread_id)
        if future is not None and not future.done():
            future.set_result(None)
        self._thread_final_text.pop(thread_id, None)
        self._thread_surface_handlers.pop(thread_id, None)
        self._thread_final_reply_handlers.pop(thread_id, None)
        self._turn_thread_ids.pop(turn_id, None)

    def _complete_turn(self, thread_id: str, turn_id: str) -> None:
        if self._thread_current_turn_id.get(thread_id, "") == turn_id:
            self._thread_current_turn_id[thread_id] = ""
        if self._active_thread_id == thread_id:
            self._active_thread_id = ""
        future = self._thread_turn_futures.pop(thread_id, None)
        if future is not None and not future.done():
            future.set_result(None)
        self._thread_final_text.pop(thread_id, None)
        self._thread_surface_handlers.pop(thread_id, None)
        self._thread_final_reply_handlers.pop(thread_id, None)
        self._turn_thread_ids.pop(turn_id, None)

    def _ensure_running(self) -> None:
        listener = self._listener_task
        if listener is not None and not listener.done():
            return
        raise RuntimeError("Runtime scheduler is not running. Start it during app startup.")

    def _raise_if_listener_failed(self) -> None:
        if self._listener_error is None:
            return
        raise RuntimeError("Single-brain event listener failed.") from self._listener_error

    def _evolve_affect_turn(self, user_text: str) -> Any | None:
        if self.affect_runtime is None:
            return None
        return self.affect_runtime.evolve(user_text=user_text)

    async def _interrupt_reply_audio_playback(self, thread_id: str) -> bool:
        context = self.runtime_tool_context
        if context is None:
            return False
        reply_audio_service = getattr(context, "reply_audio_service", None)
        interrupt_playback = getattr(reply_audio_service, "interrupt_playback", None)
        if not callable(interrupt_playback):
            return False
        maybe_awaitable = interrupt_playback()
        interrupted = (
            bool(await maybe_awaitable)
            if isawaitable(maybe_awaitable)
            else bool(maybe_awaitable)
        )
        if interrupted:
            self._thread_reply_audio_interrupted.add(thread_id)
        return interrupted

    def _reset_runtime_audio_motion(self) -> bool:
        context = self.runtime_tool_context
        if context is None:
            return False
        speech_driver = getattr(context, "speech_driver", None)
        if speech_driver is not None and hasattr(speech_driver, "reset_speech_motion"):
            try:
                return bool(speech_driver.reset_speech_motion())
            except Exception:
                return False
        head_wobbler = getattr(context, "head_wobbler", None)
        if head_wobbler is None or not hasattr(head_wobbler, "reset"):
            return False
        try:
            head_wobbler.reset()
        except Exception:
            return False
        return True

    def _consume_reply_audio_interrupted(self, thread_id: str) -> bool:
        if thread_id not in self._thread_reply_audio_interrupted:
            return False
        self._thread_reply_audio_interrupted.discard(thread_id)
        return True

    def _schedule_reply_audio_interrupt(self, thread_id: str) -> None:
        task = self._pending_reply_audio_interrupt_tasks.get(thread_id)
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self._run_reply_audio_interrupt_after_grace(thread_id))
        self._pending_reply_audio_interrupt_tasks[thread_id] = task

    def _cancel_reply_audio_interrupt_task(self, thread_id: str) -> None:
        task = self._pending_reply_audio_interrupt_tasks.pop(thread_id, None)
        if task is not None:
            task.cancel()

    async def _run_reply_audio_interrupt_after_grace(self, thread_id: str) -> None:
        current_task = asyncio.current_task()
        try:
            if self._speech_interrupt_grace_s > 0.0:
                await asyncio.sleep(self._speech_interrupt_grace_s)
            if not self._thread_user_speaking.get(thread_id, False):
                return
            interrupted = await self._interrupt_reply_audio_playback(thread_id)
            if interrupted:
                self._reset_runtime_audio_motion()
        except asyncio.CancelledError:
            raise
        finally:
            task = self._pending_reply_audio_interrupt_tasks.get(thread_id)
            if task is current_task:
                self._pending_reply_audio_interrupt_tasks.pop(thread_id, None)
