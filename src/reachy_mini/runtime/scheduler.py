"""Runtime bridge: front presentation wrapped around the resident brain kernel."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from reachy_mini.affect import (
    AffectRuntime,
    AffectState,
    AffectTurnResult,
    EmotionSignal,
    create_affect_runtime,
)
from reachy_mini.core import (
    BrainKernel,
    BrainOutput,
    BrainOutputType,
    JsonlMemoryStore,
    MemoryView,
    TaskType,
    make_id,
)
from reachy_mini.runtime.model_factory import build_front_model, build_kernel_model
from reachy_mini.runtime.tool_loader import build_runtime_tool_bundle
from reachy_mini.runtime.tools import ReachyToolContext
from reachy_mini.companion import (
    CompanionIntent,
    SurfaceExpression,
    build_affect_payload,
    build_companion_intent,
    build_companion_phase_surface_state,
    build_emotion_payload,
    build_idle_surface_state,
    build_listening_surface_state,
    build_listening_wait_surface_state,
    build_surface_expression,
)
from reachy_mini.front.events import (
    FrontSignal,
    FrontToolCall,
    FrontToolExecution,
    FrontUserTurnResult,
)
from reachy_mini.front.service import FrontService

if TYPE_CHECKING:
    from reachy_mini.runtime.config import ProfileRuntimeConfig
    from reachy_mini.runtime.profile_loader import ProfileBundle


def _build_kernel_system_prompt(
    profile: "ProfileBundle",
    *,
    workspace_root: Path | None = None,
    kernel_system_tool_names: list[str] | None = None,
    profile_tool_names: list[str] | None = None,
) -> str:
    """Compile the profile files into one kernel system prompt."""
    sections = [profile.agents_md.strip()]
    if profile.user_md.strip():
        sections.append(f"## USER\n{profile.user_md.strip()}")
    if profile.soul_md.strip():
        sections.append(f"## SOUL\n{profile.soul_md.strip()}")
    if profile.tools_md.strip():
        sections.append(f"## TOOLS\n{profile.tools_md.strip()}")
    tool_policy_lines: list[str] = []
    resolved_kernel_system_tool_names = [
        name for name in (kernel_system_tool_names or []) if str(name or "").strip()
    ]
    resolved_profile_tool_names = [
        name for name in (profile_tool_names or []) if str(name or "").strip()
    ]
    if (
        workspace_root is not None
        or resolved_kernel_system_tool_names
        or resolved_profile_tool_names
    ):
        tool_policy_lines.append("## RUNTIME_TOOL_POLICY")
        if workspace_root is not None:
            tool_policy_lines.append(f"- Current app workspace root: {workspace_root}")
        if resolved_kernel_system_tool_names:
            tool_policy_lines.append(
                f"- Kernel system tools: {', '.join(resolved_kernel_system_tool_names)}"
            )
        if resolved_profile_tool_names:
            tool_policy_lines.append(
                f"- Profile tools: {', '.join(resolved_profile_tool_names)}"
            )
        tool_policy_lines.extend(
            [
                "- If the user asks you to create, edit, inspect, search, or list files in the workspace, use the appropriate tool.",
                "- Never claim a file or workspace change succeeded unless a tool result confirms it.",
                "- If no tool has run yet, describe the next action instead of pretending it is already done.",
            ]
        )
    if tool_policy_lines:
        sections.append("\n".join(tool_policy_lines))
    return "\n\n".join(section for section in sections if section).strip()


def _default_affect_model_path() -> Path:
    """Resolve the bundled Chordia model directory."""
    return Path(__file__).resolve().parents[1] / "mode" / "Chordia"


_DEFAULT_IDLE_TICK_INTERVAL_S = 15.0
_DEFAULT_SPEECH_INTERRUPT_GRACE_S = 0.5
_SURFACED_FRONT_DECISION_SIGNAL_NAMES = {
    "idle_tick",
    "idle_entered",
    "vision_attention_updated",
}
LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class FrontOutputPacket:
    """One frontend-visible output event emitted by the runtime."""

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
        if self.type in {
            "front_hint_chunk",
            "front_hint_done",
            "front_final_chunk",
            "front_final_done",
        }:
            event["text"] = self.text
        elif self.type == "turn_error":
            event["error"] = self.error
        if self.payload:
            event["payload"] = dict(self.payload)
        return event


class RuntimeScheduler:
    """Minimal outer runtime that forwards user turns into the running kernel."""

    @classmethod
    def from_profile(
        cls,
        *,
        profile: "ProfileBundle",
        config: "ProfileRuntimeConfig",
        enable_affect: bool = True,
        runtime_tool_context: ReachyToolContext | None = None,
    ) -> "RuntimeScheduler":
        """Build a runtime scheduler directly from one loaded profile bundle."""
        kernel_model = build_kernel_model(config.kernel_model)
        tool_bundle = build_runtime_tool_bundle(
            profile,
            runtime_context=runtime_tool_context,
        )
        front = FrontService(
            profile,
            build_front_model(config.front_model),
            tools=tool_bundle.front_tools,
        )
        kernel_tools = tool_bundle.kernel_tools if hasattr(kernel_model, "bind_tools") else []
        kernel = BrainKernel(
            agent_id=profile.name,
            model=kernel_model,
            task_router_model=kernel_model,
            tools=kernel_tools,
            memory_store=JsonlMemoryStore(profile.root),
            system_prompt=_build_kernel_system_prompt(
                profile,
                workspace_root=tool_bundle.workspace_root if kernel_tools else None,
                kernel_system_tool_names=(
                    tool_bundle.kernel_system_tool_names if kernel_tools else None
                ),
                profile_tool_names=tool_bundle.profile_tool_names if kernel_tools else None,
            ),
        )
        affect_runtime = None
        if enable_affect:
            affect_runtime = create_affect_runtime(profile.root, _default_affect_model_path())
        return cls(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=affect_runtime,
            front_style=config.front_style,
            runtime_tool_context=runtime_tool_context,
        )

    def __init__(
        self,
        profile_root: Path,
        front: FrontService,
        kernel: BrainKernel,
        affect_runtime: AffectRuntime | None = None,
        front_style: str | None = None,
        idle_tick_interval_s: float = _DEFAULT_IDLE_TICK_INTERVAL_S,
        speech_interrupt_grace_s: float = _DEFAULT_SPEECH_INTERRUPT_GRACE_S,
        runtime_tool_context: ReachyToolContext | None = None,
    ) -> None:
        self.profile_root = profile_root
        self.front = front
        self.kernel = kernel
        self.affect_runtime = affect_runtime
        self.front_style = front_style
        self.runtime_tool_context = runtime_tool_context
        self._idle_tick_interval_s = max(float(idle_tick_interval_s), 0.0)
        self._speech_interrupt_grace_s = max(float(speech_interrupt_grace_s), 0.0)
        self._lifecycle_lock = asyncio.Lock()
        self._idle_condition = asyncio.Condition()
        self._listener_task: asyncio.Task[None] | None = None
        self._listener_error: BaseException | None = None
        self._pending_outputs: dict[str, asyncio.Future[BrainOutput]] = {}
        self._pending_kernel_deliveries: dict[str, dict[str, Any]] = {}
        self._delivery_tasks: set[asyncio.Task[None]] = set()
        self._idle_tick_tasks: dict[str, asyncio.Task[None]] = {}
        self._active_turns: dict[str, int] = {}
        self._thread_last_activity_at: dict[str, float] = {}
        self._thread_idle_context: dict[str, dict[str, str]] = {}
        self._thread_surface_state: dict[str, dict[str, Any]] = {}
        self._thread_front_decisions: dict[str, dict[str, Any]] = {}
        self._thread_user_speaking: dict[str, bool] = {}
        self._thread_reply_audio_interrupted: set[str] = set()
        self._pending_reply_audio_interrupt_tasks: dict[str, asyncio.Task[None]] = {}
        self._front_output_subscribers: set[asyncio.Queue[FrontOutputPacket]] = set()
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._reactive_vision_queue: asyncio.Queue[Any] | None = None
        self._reactive_vision_task: asyncio.Task[None] | None = None
        self._reactive_vision_listener: Callable[[Any], None] | None = None

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._listener_task is not None and not self._listener_task.done():
                return

            self._listener_error = None
            self._runtime_loop = asyncio.get_running_loop()
            await self.kernel.start()
            self._pending_kernel_deliveries = {}
            self._delivery_tasks = set()
            self._listener_task = asyncio.create_task(self._listen_kernel_outputs())
            self._attach_reactive_vision_bridge()

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            listener = self._listener_task
            if listener is None and not self.kernel.is_running:
                return

            await self._detach_reactive_vision_bridge()
            await self.kernel.stop()
            if listener is not None:
                try:
                    await listener
                finally:
                    self._listener_task = None
            else:
                self._listener_task = None

            self._pending_kernel_deliveries.clear()
            self._thread_last_activity_at.clear()
            self._thread_idle_context.clear()
            self._thread_surface_state.clear()
            self._thread_front_decisions.clear()
            self._thread_user_speaking.clear()
            self._thread_reply_audio_interrupted.clear()
            self._front_output_subscribers.clear()
            self._runtime_loop = None
            self._fail_pending_outputs(RuntimeError("Runtime scheduler stopped."))
            await self._cancel_delivery_tasks()
            await self._cancel_idle_tick_tasks()
            await self._cancel_reply_audio_interrupt_tasks()

    async def handle_user_text(
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
        _ = session_id
        self._ensure_running()
        self._raise_if_listener_failed()
        await self._mark_thread_active(thread_id)
        delivery_registered = False
        turn_id = make_id("turn")

        try:
            affect_turn = self._evolve_affect_turn(user_text)
            affect_state = affect_turn.state if affect_turn is not None else None
            emotion_signal = affect_turn.emotion_signal if affect_turn is not None else None
            await self._dispatch_front_signal(
                FrontSignal(
                    name="turn_started",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    user_text=user_text,
                )
            )
            await self._dispatch_front_signal(
                FrontSignal(
                    name="listening_entered",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    user_text=user_text,
                    metadata={"phase": "listening"},
                ),
                affect_state=affect_state,
                emotion_signal=emotion_signal,
                surface_state_handler=surface_state_handler,
                apply_surface_patch=True,
            )
            memory = self._build_front_memory_view(thread_id=thread_id, user_text=user_text)
            front_turn = await self.front.handle_user_turn(
                user_text=user_text,
                memory=memory,
                emotion_signal=emotion_signal,
                style=self.front_style,
            )
            if front_turn.completes_turn:
                return await self._complete_front_owned_user_turn(
                    thread_id=thread_id,
                    user_id=user_id,
                    turn_id=turn_id,
                    user_text=user_text,
                    affect_state=affect_state,
                    emotion_signal=emotion_signal,
                    surface_state_handler=surface_state_handler,
                    final_reply_handler=final_reply_handler,
                    front_turn=front_turn,
                )
            front_reply = str(front_turn.reply_text or "").strip()
            await self._publish_text_done(
                packet_type="front_hint_done",
                thread_id=thread_id,
                turn_id=turn_id,
                text=front_reply,
            )
            await self._publish_initial_front_event(
                thread_id=thread_id,
                user_id=user_id,
                turn_id=turn_id,
                user_text=user_text,
                front_reply=front_reply,
                emotion_signal=emotion_signal,
            )
            event_id = make_id("brain_event")
            self._pending_kernel_deliveries[event_id] = {
                "thread_id": thread_id,
                "user_id": user_id,
                "turn_id": turn_id,
                "user_text": user_text,
                "front_reply": front_reply,
                "affect_state": affect_state,
                "emotion_signal": emotion_signal,
                "surface_state_handler": surface_state_handler,
                "final_reply_handler": final_reply_handler,
            }
            try:
                await self.kernel.publish_user_input(
                    event_id=event_id,
                    conversation_id=thread_id,
                    user_id=user_id,
                    turn_id=turn_id,
                    text=user_text,
                    latest_front_reply=front_reply,
                )
            except Exception as exc:
                self._pending_kernel_deliveries.pop(event_id, None)
                await self._publish_turn_error(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    error=str(exc),
                )
                return front_reply
            delivery_registered = True
            return front_reply
        finally:
            if not delivery_registered:
                await self._mark_thread_idle(
                    thread_id,
                    turn_id=turn_id,
                    user_text=user_text,
                )

    async def wait_for_thread_idle(self, thread_id: str, timeout: float = 600.0) -> None:
        await asyncio.wait_for(self._wait_for_thread_idle(thread_id), timeout=timeout)
        self._raise_if_listener_failed()

    async def handle_user_speech_started(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str = "",
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        _ = session_id
        _ = user_id
        await self._handle_user_speech_signal(
            signal_name="user_speech_started",
            thread_id=thread_id,
            user_text=user_text,
            metadata={"phase": "listening"},
            surface_state_handler=surface_state_handler,
        )

    async def handle_user_speech_partial(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str = "",
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        _ = session_id
        _ = user_id
        self._ensure_running()
        self._raise_if_listener_failed()
        self._cancel_idle_tick_task(thread_id)
        self._record_thread_activity(thread_id)

        was_user_speaking = self._thread_user_speaking.get(thread_id, False)
        self._thread_user_speaking[thread_id] = True
        if not was_user_speaking:
            self._schedule_reply_audio_interrupt(thread_id)

        resolved_user_text = str(user_text or "")
        self._thread_idle_context[thread_id] = {
            "turn_id": "",
            "user_text": resolved_user_text,
        }
        current_phase = str(
            self._thread_surface_state.get(thread_id, {}).get("phase", "") or ""
        )
        await self._dispatch_front_signal(
            FrontSignal(
                name="user_speech_partial",
                thread_id=thread_id,
                user_text=resolved_user_text,
                metadata={
                    "phase": "listening",
                    "partial": True,
                },
            ),
            surface_state_handler=surface_state_handler,
            apply_surface_patch=current_phase != "listening",
        )

    async def handle_user_speech_stopped(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str = "",
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        _ = session_id
        _ = user_id
        await self._handle_user_speech_signal(
            signal_name="user_speech_stopped",
            thread_id=thread_id,
            user_text=user_text,
            metadata={"phase": "listening_wait"},
            surface_state_handler=surface_state_handler,
        )

    async def _wait_for_thread_idle(self, thread_id: str) -> None:
        async with self._idle_condition:
            while self._active_turns.get(thread_id, 0) > 0:
                await self._idle_condition.wait()

    async def _handle_user_speech_signal(
        self,
        *,
        signal_name: str,
        thread_id: str,
        user_text: str,
        metadata: dict[str, Any],
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        self._ensure_running()
        self._raise_if_listener_failed()
        self._cancel_idle_tick_task(thread_id)
        self._record_thread_activity(thread_id)
        is_speech_start = signal_name == "user_speech_started"
        self._thread_user_speaking[thread_id] = is_speech_start
        if is_speech_start:
            self._schedule_reply_audio_interrupt(thread_id)
        else:
            self._cancel_reply_audio_interrupt_task(thread_id)
        self._thread_idle_context[thread_id] = {
            "turn_id": "",
            "user_text": str(user_text or ""),
        }
        await self._dispatch_front_signal(
            FrontSignal(
                name=signal_name,
                thread_id=thread_id,
                user_text=str(user_text or ""),
                metadata=dict(metadata),
            ),
            surface_state_handler=surface_state_handler,
            apply_surface_patch=True,
        )
        if signal_name == "user_speech_stopped" and self._active_turns.get(thread_id, 0) <= 0:
            self._schedule_idle_tick(thread_id)

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

    def get_thread_surface_state(self, thread_id: str) -> dict[str, Any] | None:
        state = self._thread_surface_state.get(thread_id)
        if state is None:
            return None
        return dict(state)

    def get_thread_front_decision(self, thread_id: str) -> dict[str, Any] | None:
        decision = self._thread_front_decisions.get(thread_id)
        if decision is None:
            return None
        return dict(decision)

    def subscribe_front_outputs(self) -> asyncio.Queue[FrontOutputPacket]:
        queue: asyncio.Queue[FrontOutputPacket] = asyncio.Queue()
        self._front_output_subscribers.add(queue)
        return queue

    def unsubscribe_front_outputs(self, queue: asyncio.Queue[FrontOutputPacket]) -> None:
        self._front_output_subscribers.discard(queue)

    async def _listen_kernel_outputs(self) -> None:
        try:
            while True:
                output = await self.kernel.recv_output()
                future = self._pending_outputs.pop(output.event_id, None)
                if future is not None and not future.done():
                    future.set_result(output)
                    continue

                delivery = self._pending_kernel_deliveries.pop(output.event_id, None)
                if delivery is not None:
                    self._schedule_kernel_delivery(output=output, delivery=delivery)
                    continue

                if output.type == BrainOutputType.recorded:
                    continue

                if output.type == BrainOutputType.stopped:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._listener_error = exc
            self._fail_pending_outputs(exc)
            raise
        finally:
            if self._listener_error is None:
                self._fail_pending_outputs(RuntimeError("Brain kernel listener stopped."))
            self._listener_task = None

    async def _await_kernel_output(self, future: asyncio.Future[BrainOutput], event_id: str) -> BrainOutput:
        try:
            return await future
        finally:
            self._pending_outputs.pop(event_id, None)

    def _register_output_waiter(self, event_id: str) -> asyncio.Future[BrainOutput]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BrainOutput] = loop.create_future()
        self._pending_outputs[event_id] = future
        return future

    def _render_kernel_output(self, output: BrainOutput) -> str:
        if output.type == BrainOutputType.response and output.response is not None:
            if output.response.task_type == TaskType.none:
                return ""
            reply = str(output.response.reply or "").strip()
            if reply:
                return reply

            if output.response.pending_tool_calls:
                tool_names = ", ".join(
                    call.tool_name for call in output.response.pending_tool_calls if call.tool_name
                ).strip()
                if tool_names:
                    return f"内核正在等待外部工具结果后继续：{tool_names}"
                return "内核正在等待外部工具结果后继续。"

            if output.response.run is not None:
                return str(output.response.run.result_summary or "").strip()
            return ""

        if output.type == BrainOutputType.error:
            error = str(output.error or "").strip() or "unknown error"
            return f"内核处理失败：{error}"

        return ""

    def _fail_pending_outputs(self, error: BaseException) -> None:
        pending = list(self._pending_outputs.values())
        self._pending_outputs.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    async def _cancel_delivery_tasks(self) -> None:
        tasks = list(self._delivery_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._delivery_tasks.clear()

    async def _cancel_idle_tick_tasks(self) -> None:
        tasks = list(self._idle_tick_tasks.values())
        self._idle_tick_tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _cancel_reply_audio_interrupt_tasks(self) -> None:
        tasks = list(self._pending_reply_audio_interrupt_tasks.values())
        self._pending_reply_audio_interrupt_tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _schedule_kernel_delivery(self, *, output: BrainOutput, delivery: dict[str, Any]) -> None:
        task = asyncio.create_task(self._deliver_kernel_output(output=output, delivery=delivery))
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    def _raise_if_listener_failed(self) -> None:
        if self._listener_error is None:
            return
        raise RuntimeError("Brain kernel output listener failed.") from self._listener_error

    def _ensure_running(self) -> None:
        listener = self._listener_task
        if listener is not None and not listener.done() and self.kernel.is_running:
            return
        raise RuntimeError("Runtime scheduler is not running. Start it during app startup.")

    async def _mark_thread_active(self, thread_id: str) -> None:
        self._cancel_idle_tick_task(thread_id)
        self._record_thread_activity(thread_id)
        async with self._idle_condition:
            self._active_turns[thread_id] = self._active_turns.get(thread_id, 0) + 1

    async def _mark_thread_idle(
        self,
        thread_id: str,
        *,
        turn_id: str = "",
        user_text: str = "",
    ) -> None:
        became_idle = False
        async with self._idle_condition:
            count = self._active_turns.get(thread_id, 0)
            if count <= 1:
                self._active_turns.pop(thread_id, None)
                became_idle = True
            else:
                self._active_turns[thread_id] = count - 1
            self._idle_condition.notify_all()
        if not became_idle:
            return
        self._cancel_reply_audio_interrupt_task(thread_id)
        self._thread_reply_audio_interrupted.discard(thread_id)
        self._thread_idle_context[thread_id] = {
            "turn_id": str(turn_id or ""),
            "user_text": str(user_text or ""),
        }
        self._record_thread_activity(thread_id)
        self._schedule_idle_tick(thread_id)

    def _record_thread_activity(self, thread_id: str) -> None:
        self._thread_last_activity_at[thread_id] = time.monotonic()

    def _cancel_idle_tick_task(self, thread_id: str) -> None:
        task = self._idle_tick_tasks.pop(thread_id, None)
        if task is not None:
            task.cancel()

    def _schedule_reply_audio_interrupt(self, thread_id: str) -> None:
        if self._active_turns.get(thread_id, 0) <= 0:
            return
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
            if self._active_turns.get(thread_id, 0) <= 0:
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

    def _attach_reactive_vision_bridge(self) -> None:
        context = self.runtime_tool_context
        if context is None:
            return
        camera_worker = getattr(context, "camera_worker", None)
        if camera_worker is None or not hasattr(camera_worker, "add_reactive_vision_listener"):
            return
        if self._reactive_vision_task is not None and not self._reactive_vision_task.done():
            return

        self._reactive_vision_queue = asyncio.Queue()
        self._reactive_vision_task = asyncio.create_task(self._run_reactive_vision_loop())
        runtime_loop = self._runtime_loop

        def _listener(event: Any) -> None:
            queue = self._reactive_vision_queue
            if runtime_loop is None or queue is None:
                return
            try:
                runtime_loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                return

        self._reactive_vision_listener = _listener
        camera_worker.add_reactive_vision_listener(_listener)

    async def _detach_reactive_vision_bridge(self) -> None:
        context = self.runtime_tool_context
        camera_worker = getattr(context, "camera_worker", None) if context is not None else None
        listener = self._reactive_vision_listener
        if (
            listener is not None
            and camera_worker is not None
            and hasattr(camera_worker, "remove_reactive_vision_listener")
        ):
            try:
                camera_worker.remove_reactive_vision_listener(listener)
            except Exception:
                pass
        self._reactive_vision_listener = None

        task = self._reactive_vision_task
        self._reactive_vision_task = None
        self._reactive_vision_queue = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_reactive_vision_loop(self) -> None:
        queue = self._reactive_vision_queue
        if queue is None:
            return
        try:
            while True:
                event = await queue.get()
                try:
                    await self._handle_reactive_vision_event(event)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise

    async def _handle_reactive_vision_event(self, event: Any) -> None:
        signal = self._adapt_reactive_vision_signal(event)
        if signal is None:
            return
        await self._dispatch_front_signal(
            signal,
            apply_surface_patch=True,
        )

    def _adapt_reactive_vision_signal(self, event: Any) -> FrontSignal | None:
        event_name = str(getattr(event, "name", "") or "").strip()
        metadata = dict(getattr(event, "metadata", {}) or {})
        if event_name not in {"attention_acquired", "attention_released"}:
            return None

        thread_id = self._resolve_reactive_vision_thread_id()
        if not self._reactive_vision_can_affect_front(thread_id):
            return None

        idle_context = dict(self._thread_idle_context.get(thread_id, {}))
        base_metadata = {
            "source": str(metadata.get("source", "reactive_vision") or "reactive_vision"),
            "reactive_event_name": event_name,
        }

        if event_name == "attention_acquired":
            if metadata.get("direction") is not None:
                base_metadata["direction"] = metadata.get("direction")
            if metadata.get("tracking_enabled") is not None:
                base_metadata["tracking_enabled"] = bool(metadata.get("tracking_enabled"))
            return FrontSignal(
                name="vision_attention_updated",
                thread_id=thread_id,
                turn_id=str(idle_context.get("turn_id", "") or ""),
                user_text=str(idle_context.get("user_text", "") or ""),
                metadata=base_metadata,
            )

        return FrontSignal(
            name="idle_entered",
            thread_id=thread_id,
            turn_id=str(idle_context.get("turn_id", "") or ""),
            user_text=str(idle_context.get("user_text", "") or ""),
            metadata={
                **base_metadata,
                "phase": "idle",
                "reason": str(metadata.get("reason", "") or "released"),
                "return_to_center": bool(metadata.get("return_to_center", False)),
            },
        )

    def _resolve_reactive_vision_thread_id(self) -> str:
        if self._thread_last_activity_at:
            return max(
                self._thread_last_activity_at,
                key=lambda thread_id: self._thread_last_activity_at.get(thread_id, 0.0),
            )
        if self._thread_surface_state:
            return next(iter(self._thread_surface_state))
        if self._thread_idle_context:
            return next(iter(self._thread_idle_context))
        return "app:main"

    def _reactive_vision_can_affect_front(self, thread_id: str) -> bool:
        current_phase = str(
            self._thread_surface_state.get(thread_id, {}).get("phase", "") or ""
        ).strip().lower()
        return current_phase not in {
            "listening",
            "listening_wait",
            "replying",
            "settling",
        }

    def _schedule_idle_tick(self, thread_id: str) -> None:
        if self._idle_tick_interval_s <= 0.0:
            return
        self._cancel_idle_tick_task(thread_id)
        task = asyncio.create_task(self._run_idle_tick_loop(thread_id))
        self._idle_tick_tasks[thread_id] = task

        def _cleanup(done_task: asyncio.Task[None], *, owned_thread_id: str = thread_id) -> None:
            if self._idle_tick_tasks.get(owned_thread_id) is done_task:
                self._idle_tick_tasks.pop(owned_thread_id, None)

        task.add_done_callback(_cleanup)

    async def _run_idle_tick_loop(self, thread_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(self._idle_tick_interval_s)
                if self._active_turns.get(thread_id, 0) > 0:
                    return
                idle_context = dict(self._thread_idle_context.get(thread_id, {}))
                idle_since = self._thread_last_activity_at.get(thread_id, time.monotonic())
                await self._dispatch_front_signal(
                    FrontSignal(
                        name="idle_tick",
                        thread_id=thread_id,
                        turn_id=str(idle_context.get("turn_id", "") or ""),
                        user_text=str(idle_context.get("user_text", "") or ""),
                        metadata={
                            "phase": "idle",
                            "idle_seconds": round(max(time.monotonic() - idle_since, 0.0), 3),
                        },
                    )
                )
        except asyncio.CancelledError:
            raise

    async def _push_surface_state(
        self,
        thread_id: str,
        state: dict[str, Any],
        *,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        self._thread_surface_state[thread_id] = dict(state)
        if surface_state_handler is not None:
            maybe_awaitable = surface_state_handler(dict(state))
            if isawaitable(maybe_awaitable):
                await maybe_awaitable

    def _evolve_affect_turn(self, user_text: str) -> AffectTurnResult | None:
        if self.affect_runtime is None:
            return None
        return self.affect_runtime.evolve(user_text=user_text)

    async def _complete_front_owned_user_turn(
        self,
        *,
        thread_id: str,
        user_id: str,
        turn_id: str,
        user_text: str,
        affect_state: AffectState | None,
        emotion_signal: EmotionSignal | None,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
        final_reply_handler: Callable[[dict[str, Any]], Awaitable[bool] | bool | None]
        | None,
        front_turn: FrontUserTurnResult,
    ) -> str:
        tool_calls_payload = [
            {
                "tool_name": str(tool_call.tool_name or "").strip(),
                "arguments": dict(tool_call.arguments or {}),
                "reason": str(tool_call.reason or ""),
            }
            for tool_call in list(front_turn.tool_calls or [])
            if str(tool_call.tool_name or "").strip()
        ]
        decision_payload = {
            "signal_name": "user_turn",
            "turn_id": turn_id,
            "reply_text": str(front_turn.reply_text or "").strip(),
            "lifecycle_state": "replying",
            "surface_patch": {
                "phase": "replying",
                "recommended_hold_ms": 0,
                "source_signal": "user_turn",
            },
            "tool_calls": tool_calls_payload,
            "debug_reason": str(front_turn.debug_reason or ""),
        }
        self._thread_front_decisions[thread_id] = dict(decision_payload)
        self._publish_front_output(
            FrontOutputPacket(
                type="front_decision",
                thread_id=thread_id,
                turn_id=turn_id,
                payload=dict(decision_payload),
            )
        )
        await self._apply_front_decision_surface_state(
            thread_id=thread_id,
            decision_payload=decision_payload,
            affect_state=affect_state,
            emotion_signal=emotion_signal,
            surface_state_handler=surface_state_handler,
        )

        tool_results = list(front_turn.tool_results or [])
        await self._publish_front_tool_results(
            thread_id=thread_id,
            turn_id=turn_id,
            signal_name="user_turn",
            tool_results=tool_results,
        )
        final_reply = str(front_turn.reply_text or "").strip()
        if not final_reply:
            try:
                final_reply = self.front.render_user_turn_reply(
                    user_text=user_text,
                    tool_results=tool_results,
                ).strip()
            except Exception as exc:
                await self._publish_turn_error(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    error=str(exc),
                )
                final_reply = ""

        if not final_reply:
            first_error = next(
                (
                    result.result.strip()
                    for result in tool_results
                    if not result.success and result.result.strip()
                ),
                "",
            )
            if first_error:
                final_reply = first_error
            elif tool_results and all(result.success for result in tool_results):
                final_reply = "这一步我已经按你的要求处理好了。"
            elif tool_results:
                final_reply = "这一步我试了，不过没有完全成功。"

        if final_reply.strip():
            companion_intent = build_companion_intent(
                user_text=user_text,
                kernel_output=final_reply,
                affect_state=affect_state,
                emotion_signal=emotion_signal,
            )
            surface_expression = build_surface_expression(
                companion_intent,
                affect_state=affect_state,
            )
            await self._record_front_delivery(
                thread_id=thread_id,
                user_id=user_id,
                turn_id=turn_id,
                user_text=user_text,
                front_reply=final_reply,
                kernel_output="",
                affect_state=affect_state,
                emotion_signal=emotion_signal,
                companion_intent=companion_intent,
                surface_expression=surface_expression,
                wait_for_record=False,
                source="runtime_front_only",
            )

        await self._publish_text_done(
            packet_type="front_final_done",
            thread_id=thread_id,
            turn_id=turn_id,
            text=final_reply,
        )
        await self._invoke_final_reply_handler(
            handler=final_reply_handler,
            thread_id=thread_id,
            turn_id=turn_id,
            user_text=user_text,
            text=final_reply,
        )
        if self._consume_reply_audio_interrupted(thread_id):
            return final_reply
        if self._thread_user_speaking.get(thread_id, False):
            return final_reply

        await self._dispatch_front_signal(
            FrontSignal(
                name="settling_entered",
                thread_id=thread_id,
                turn_id=turn_id,
                user_text=user_text,
                metadata={"phase": "settling"},
            ),
            affect_state=affect_state,
            emotion_signal=emotion_signal,
            surface_state_handler=surface_state_handler,
            apply_surface_patch=True,
        )
        await self._dispatch_front_signal(
            FrontSignal(
                name="idle_entered",
                thread_id=thread_id,
                turn_id=turn_id,
                user_text=user_text,
                metadata={"phase": "idle"},
            ),
            affect_state=affect_state,
            emotion_signal=emotion_signal,
            surface_state_handler=surface_state_handler,
            apply_surface_patch=True,
        )
        return final_reply

    def _build_front_memory_view(self, *, thread_id: str, user_text: str) -> MemoryView:
        memory_store = getattr(self.kernel, "memory_store", None)
        if memory_store is None:
            return MemoryView()
        try:
            return memory_store.build_memory_view(thread_id, self.kernel.agent_id, user_text)
        except Exception:
            return MemoryView()

    def _build_front_stream_handler(
        self,
        *,
        thread_id: str,
        turn_id: str,
        packet_type: str,
    ) -> Callable[[str], Awaitable[None]]:
        async def _emit(chunk: str) -> None:
            text = str(chunk or "")
            if not text:
                return
            self._publish_front_output(
                FrontOutputPacket(
                    type=packet_type,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    text=text,
                )
            )

        return _emit

    def _publish_front_output(self, packet: FrontOutputPacket) -> None:
        for subscriber in list(self._front_output_subscribers):
            subscriber.put_nowait(packet)

    @staticmethod
    def _extract_front_result_value(result: Any, key: str, default: Any = None) -> Any:
        if isinstance(result, dict):
            return result.get(key, default)
        return getattr(result, key, default)

    async def _dispatch_front_signal(
        self,
        signal: FrontSignal,
        *,
        affect_state: AffectState | None = None,
        emotion_signal: EmotionSignal | None = None,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        apply_surface_patch: bool = False,
    ) -> dict[str, Any] | None:
        handler = getattr(self.front, "handle_signal", None)
        if not callable(handler):
            return None
        try:
            maybe_awaitable = handler(signal)
            if isawaitable(maybe_awaitable):
                result = await maybe_awaitable
            else:
                result = maybe_awaitable
            if result is not None:
                decision_payload = self._normalize_front_decision_payload(
                    signal=signal,
                    result=result,
                )
                self._thread_front_decisions[signal.thread_id] = dict(decision_payload)
                if apply_surface_patch:
                    await self._apply_front_decision_surface_state(
                        thread_id=signal.thread_id,
                        decision_payload=decision_payload,
                        affect_state=affect_state,
                        emotion_signal=emotion_signal,
                        surface_state_handler=surface_state_handler,
                    )
                if self._should_surface_front_decision(
                    signal_name=str(decision_payload["signal_name"] or ""),
                    reply_text=str(decision_payload["reply_text"] or ""),
                    tool_calls=list(decision_payload["tool_calls"] or []),
                ):
                    self._publish_front_output(
                        FrontOutputPacket(
                            type="front_decision",
                            thread_id=signal.thread_id,
                            turn_id=str(decision_payload["turn_id"] or ""),
                            payload=dict(decision_payload),
                        )
                    )
                await self._execute_front_tool_calls(
                    thread_id=signal.thread_id,
                    turn_id=str(decision_payload["turn_id"] or ""),
                    signal_name=str(decision_payload["signal_name"] or ""),
                    tool_calls=list(decision_payload["tool_calls"] or []),
                )
                return decision_payload
        except Exception:
            return None
        return None

    def _normalize_front_decision_payload(
        self,
        *,
        signal: FrontSignal,
        result: Any,
    ) -> dict[str, Any]:
        tool_calls = self._extract_front_result_value(result, "tool_calls", []) or []
        resolved_signal_name = str(
            self._extract_front_result_value(result, "signal_name", "") or signal.name
        )
        resolved_turn_id = str(
            self._extract_front_result_value(result, "turn_id", "") or signal.turn_id
        )
        resolved_lifecycle_state = str(
            self._extract_front_result_value(result, "lifecycle_state", "")
            or signal.metadata.get("phase", "")
            or ""
        )
        normalized_tool_calls = [
            {
                "tool_name": str(
                    self._extract_front_result_value(call, "tool_name", "") or ""
                ),
                "arguments": dict(
                    self._extract_front_result_value(call, "arguments", {}) or {}
                ),
                "reason": str(
                    self._extract_front_result_value(call, "reason", "") or ""
                ),
            }
            for call in list(tool_calls)
        ]
        surface_patch = dict(
            self._extract_front_result_value(result, "surface_patch", {}) or {}
        )
        if resolved_lifecycle_state and not str(surface_patch.get("phase", "") or "").strip():
            surface_patch["phase"] = resolved_lifecycle_state
        if resolved_signal_name and not str(surface_patch.get("source_signal", "") or "").strip():
            surface_patch["source_signal"] = resolved_signal_name
        return {
            "signal_name": resolved_signal_name,
            "turn_id": resolved_turn_id,
            "reply_text": str(self._extract_front_result_value(result, "reply_text", "") or ""),
            "lifecycle_state": resolved_lifecycle_state,
            "surface_patch": surface_patch,
            "tool_calls": normalized_tool_calls,
            "debug_reason": str(self._extract_front_result_value(result, "debug_reason", "") or ""),
            "signal_metadata": dict(signal.metadata or {}),
        }

    async def _apply_front_decision_surface_state(
        self,
        *,
        thread_id: str,
        decision_payload: dict[str, Any],
        affect_state: AffectState | None,
        emotion_signal: EmotionSignal | None,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        state = self._build_surface_state_from_front_decision(
            thread_id=thread_id,
            decision_payload=decision_payload,
            affect_state=affect_state,
            emotion_signal=emotion_signal,
        )
        if state is None:
            return
        await self._push_surface_state(
            thread_id,
            state,
            surface_state_handler=surface_state_handler,
        )

    def _build_surface_state_from_front_decision(
        self,
        *,
        thread_id: str,
        decision_payload: dict[str, Any],
        affect_state: AffectState | None,
        emotion_signal: EmotionSignal | None,
    ) -> dict[str, Any] | None:
        surface_patch = dict(decision_payload.get("surface_patch", {}) or {})
        phase = str(
            surface_patch.get("phase", "")
            or decision_payload.get("lifecycle_state", "")
            or ""
        ).strip().lower()
        if not phase:
            return None

        if phase == "listening":
            state = build_listening_surface_state(
                thread_id=thread_id,
                affect_state=affect_state,
                emotion_signal=emotion_signal,
            )
        elif phase == "listening_wait":
            state = build_listening_wait_surface_state(
                thread_id=thread_id,
                affect_state=affect_state,
                emotion_signal=emotion_signal,
            )
        elif phase == "idle":
            state = build_idle_surface_state(
                thread_id=thread_id,
                affect_state=affect_state,
                emotion_signal=emotion_signal,
            )
        else:
            state = build_companion_phase_surface_state(
                thread_id=thread_id,
                phase=phase,
                affect_state=affect_state,
                emotion_signal=emotion_signal,
            )

        for key, value in surface_patch.items():
            if key == "thread_id" or value is None:
                continue
            state[key] = value
        state["thread_id"] = thread_id
        return state

    @staticmethod
    def _should_surface_front_decision(
        *,
        signal_name: str,
        reply_text: str,
        tool_calls: list[dict[str, Any]],
    ) -> bool:
        if str(reply_text or "").strip():
            return True
        if not list(tool_calls or []):
            return False
        return str(signal_name or "").strip() in _SURFACED_FRONT_DECISION_SIGNAL_NAMES

    async def _execute_front_tool_calls(
        self,
        *,
        thread_id: str,
        turn_id: str,
        signal_name: str,
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        executor = getattr(self.front, "execute_tool_calls", None)
        if not callable(executor):
            return []

        normalized_calls = [
            FrontToolCall(
                tool_name=str(tool_call.get("tool_name", "") or "").strip(),
                arguments=dict(tool_call.get("arguments", {}) or {}),
                reason=str(tool_call.get("reason", "") or ""),
            )
            for tool_call in list(tool_calls or [])
            if str(tool_call.get("tool_name", "") or "").strip()
        ]
        execution_results = executor(normalized_calls)
        if isawaitable(execution_results):
            execution_results = await execution_results

        published_results: list[dict[str, Any]] = []
        for execution_result in list(execution_results or []):
            payload = {
                "signal_name": signal_name,
                "tool_name": str(
                    self._extract_front_result_value(execution_result, "tool_name", "") or ""
                ),
                "arguments": dict(
                    self._extract_front_result_value(execution_result, "arguments", {}) or {}
                ),
                "reason": str(
                    self._extract_front_result_value(execution_result, "reason", "") or ""
                ),
                "success": bool(
                    self._extract_front_result_value(execution_result, "success", False)
                ),
                "result": str(
                    self._extract_front_result_value(execution_result, "result", "") or ""
                ),
            }
            published_results.append(payload)
            self._publish_front_output(
                FrontOutputPacket(
                    type="front_tool_result",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    payload=payload,
                )
            )
        return published_results

    async def _publish_front_tool_results(
        self,
        *,
        thread_id: str,
        turn_id: str,
        signal_name: str,
        tool_results: list[FrontToolExecution],
    ) -> list[dict[str, Any]]:
        published_results: list[dict[str, Any]] = []
        for execution_result in list(tool_results or []):
            payload = {
                "signal_name": signal_name,
                "tool_name": str(
                    self._extract_front_result_value(execution_result, "tool_name", "") or ""
                ),
                "arguments": dict(
                    self._extract_front_result_value(execution_result, "arguments", {}) or {}
                ),
                "reason": str(
                    self._extract_front_result_value(execution_result, "reason", "") or ""
                ),
                "success": bool(
                    self._extract_front_result_value(execution_result, "success", False)
                ),
                "result": str(
                    self._extract_front_result_value(execution_result, "result", "") or ""
                ),
            }
            published_results.append(payload)
            self._publish_front_output(
                FrontOutputPacket(
                    type="front_tool_result",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    payload=payload,
                )
            )
        return published_results

    async def _publish_text_done(
        self,
        *,
        packet_type: str,
        thread_id: str,
        turn_id: str,
        text: str,
    ) -> None:
        self._publish_front_output(
            FrontOutputPacket(
                type=packet_type,
                thread_id=thread_id,
                turn_id=turn_id,
                text=str(text or "").strip(),
            )
        )

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

    async def _publish_initial_front_event(
        self,
        *,
        thread_id: str,
        user_id: str,
        turn_id: str,
        user_text: str,
        front_reply: str,
        emotion_signal: EmotionSignal | None,
    ) -> None:
        if not front_reply.strip():
            return
        metadata = {
            "source": "runtime_front_hint",
        }
        metadata.update(build_emotion_payload(emotion_signal))
        try:
            await self.kernel.publish_front_event(
                conversation_id=thread_id,
                user_id=user_id,
                turn_id=turn_id,
                front_event={
                    "event_type": "dialogue",
                    "user_text": user_text,
                    "front_reply": front_reply,
                    "emotion": emotion_signal.primary_emotion if emotion_signal is not None else "",
                    "tags": [
                        tag
                        for tag in [
                            emotion_signal.primary_emotion if emotion_signal is not None else "",
                            emotion_signal.support_need if emotion_signal is not None else "",
                        ]
                        if str(tag or "").strip()
                    ],
                    "metadata": metadata,
                },
            )
        except Exception:
            return

    async def _deliver_kernel_output(self, *, output: BrainOutput, delivery: dict[str, Any]) -> None:
        try:
            thread_id = str(delivery["thread_id"])
            turn_id = str(delivery["turn_id"])
            user_text = str(delivery["user_text"])
            front_reply = str(delivery.get("front_reply", "") or "").strip()
            if (
                output.type == BrainOutputType.response
                and output.response is not None
                and output.response.task_type == TaskType.none
            ):
                await self._publish_text_done(
                    packet_type="front_final_done",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    text=front_reply,
                )
                if front_reply:
                    await self._invoke_final_reply_handler(
                        handler=delivery.get("final_reply_handler"),
                        thread_id=thread_id,
                        turn_id=turn_id,
                        user_text=user_text,
                        text=front_reply,
                    )
                await self._dispatch_front_signal(
                    FrontSignal(
                        name="idle_entered",
                        thread_id=thread_id,
                        turn_id=turn_id,
                        user_text=user_text,
                        metadata={"phase": "idle"},
                    ),
                    affect_state=delivery["affect_state"],
                    emotion_signal=delivery["emotion_signal"],
                    surface_state_handler=delivery["surface_state_handler"],
                    apply_surface_patch=True,
                )
                return

            kernel_output = self._render_kernel_output(output)
            if not kernel_output:
                await self._publish_text_done(
                    packet_type="front_final_done",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    text=front_reply,
                )
                if front_reply:
                    await self._invoke_final_reply_handler(
                        handler=delivery.get("final_reply_handler"),
                        thread_id=thread_id,
                        turn_id=turn_id,
                        user_text=user_text,
                        text=front_reply,
                    )
                await self._dispatch_front_signal(
                    FrontSignal(
                        name="idle_entered",
                        thread_id=thread_id,
                        turn_id=turn_id,
                        user_text=user_text,
                        metadata={"phase": "idle"},
                    ),
                    affect_state=delivery["affect_state"],
                    emotion_signal=delivery["emotion_signal"],
                    surface_state_handler=delivery["surface_state_handler"],
                    apply_surface_patch=True,
                )
                return

            companion_intent = build_companion_intent(
                user_text=user_text,
                kernel_output=kernel_output,
                affect_state=delivery["affect_state"],
                emotion_signal=delivery["emotion_signal"],
            )
            surface_expression = build_surface_expression(
                companion_intent,
                affect_state=delivery["affect_state"],
            )
            await self._dispatch_front_signal(
                FrontSignal(
                    name="kernel_output_ready",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    user_text=user_text,
                    metadata={
                        "phase": "replying",
                        "kernel_output": kernel_output,
                    },
                ),
                affect_state=delivery["affect_state"],
                emotion_signal=delivery["emotion_signal"],
                surface_state_handler=delivery["surface_state_handler"],
                apply_surface_patch=True,
            )

            try:
                presented = await self.front.present(
                    user_text=user_text,
                    kernel_output=kernel_output,
                    affect_state=delivery["affect_state"],
                    emotion_signal=delivery["emotion_signal"],
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                    stream_handler=self._build_front_stream_handler(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        packet_type="front_final_chunk",
                    ),
                    style=self.front_style,
                )
            except Exception as exc:
                fallback = kernel_output
                await self._publish_text_done(
                    packet_type="front_final_done",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    text=fallback,
                )
                await self._dispatch_front_signal(
                    FrontSignal(
                        name="idle_entered",
                        thread_id=thread_id,
                        turn_id=turn_id,
                        user_text=user_text,
                        metadata={"phase": "idle"},
                    ),
                    affect_state=delivery["affect_state"],
                    emotion_signal=delivery["emotion_signal"],
                    surface_state_handler=delivery["surface_state_handler"],
                    apply_surface_patch=True,
                )
                await self._publish_turn_error(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    error=str(exc),
                )
                return

            final_reply = presented.strip() or kernel_output
            await self._record_front_delivery(
                thread_id=thread_id,
                user_id=str(delivery["user_id"]),
                turn_id=turn_id,
                user_text=user_text,
                front_reply=final_reply,
                kernel_output=kernel_output,
                affect_state=delivery["affect_state"],
                emotion_signal=delivery["emotion_signal"],
                companion_intent=companion_intent,
                surface_expression=surface_expression,
                wait_for_record=False,
            )
            await self._publish_text_done(
                packet_type="front_final_done",
                thread_id=thread_id,
                turn_id=turn_id,
                text=final_reply,
            )
            await self._invoke_final_reply_handler(
                handler=delivery.get("final_reply_handler"),
                thread_id=thread_id,
                turn_id=turn_id,
                user_text=user_text,
                text=final_reply,
            )
            if self._consume_reply_audio_interrupted(thread_id):
                return
            if self._thread_user_speaking.get(thread_id, False):
                return
            await self._dispatch_front_signal(
                FrontSignal(
                    name="settling_entered",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    user_text=user_text,
                    metadata={"phase": "settling"},
                ),
                affect_state=delivery["affect_state"],
                emotion_signal=delivery["emotion_signal"],
                surface_state_handler=delivery["surface_state_handler"],
                apply_surface_patch=True,
            )
            await self._dispatch_front_signal(
                FrontSignal(
                    name="idle_entered",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    user_text=user_text,
                    metadata={"phase": "idle"},
                ),
                affect_state=delivery["affect_state"],
                emotion_signal=delivery["emotion_signal"],
                surface_state_handler=delivery["surface_state_handler"],
                apply_surface_patch=True,
            )
        finally:
            await self._mark_thread_idle(
                str(delivery["thread_id"]),
                turn_id=str(delivery.get("turn_id", "") or ""),
                user_text=str(delivery.get("user_text", "") or ""),
            )

    async def _record_front_delivery(
        self,
        *,
        thread_id: str,
        user_id: str,
        turn_id: str,
        user_text: str,
        front_reply: str,
        kernel_output: str,
        affect_state: AffectState | None,
        emotion_signal: EmotionSignal | None,
        companion_intent: CompanionIntent,
        surface_expression: SurfaceExpression,
        wait_for_record: bool = True,
        source: str = "runtime_scheduler",
    ) -> None:
        if not front_reply.strip():
            return

        metadata: dict[str, Any] = {
            "source": source,
            "kernel_output": kernel_output,
            "mode": companion_intent.mode,
            "warmth": companion_intent.warmth,
            "initiative": companion_intent.initiative,
            "intensity": companion_intent.intensity,
            "text_style": surface_expression.text_style,
            "expression": surface_expression.expression,
        }
        metadata.update(build_affect_payload(affect_state))
        metadata.update(build_emotion_payload(emotion_signal))

        try:
            event_id = make_id("brain_event")
            future = self._register_output_waiter(event_id) if wait_for_record else None
            await self.kernel.publish_front_event(
                event_id=event_id,
                conversation_id=thread_id,
                user_id=user_id,
                turn_id=turn_id,
                front_event={
                    "event_type": "dialogue",
                    "user_text": user_text,
                    "front_reply": front_reply,
                    "emotion": surface_expression.expression,
                    "tags": [
                        tag
                        for tag in [
                            companion_intent.mode,
                            surface_expression.text_style,
                            surface_expression.expression,
                            emotion_signal.primary_emotion if emotion_signal is not None else "",
                            emotion_signal.support_need if emotion_signal is not None else "",
                        ]
                        if str(tag or "").strip()
                    ],
                    "metadata": metadata,
                },
            )
            if wait_for_record and future is not None:
                await self._await_kernel_output(future, event_id)
        except Exception:
            self._pending_outputs.pop(event_id, None)
            return

    async def _invoke_final_reply_handler(
        self,
        *,
        handler: Callable[[dict[str, Any]], Awaitable[bool] | bool | None] | None,
        thread_id: str,
        turn_id: str,
        user_text: str,
        text: str,
    ) -> bool:
        """Optionally synthesize or play one final reply before settling."""

        if handler is None:
            LOGGER.info("Reply audio handler skipped: no handler provided.")
            return False
        if self._thread_user_speaking.get(thread_id, False):
            LOGGER.info("Reply audio handler skipped: user still marked speaking on %s.", thread_id)
            return False
        if thread_id in self._thread_reply_audio_interrupted:
            LOGGER.info("Reply audio handler skipped: thread %s already interrupted.", thread_id)
            return False

        started_emitted = False
        finished_emitted = False
        audio_interrupted = False

        async def _emit_audio_started() -> None:
            nonlocal started_emitted
            if started_emitted:
                return
            started_emitted = True
            self._record_thread_activity(thread_id)
            await self._dispatch_front_signal(
                FrontSignal(
                    name="assistant_audio_started",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    user_text=user_text,
                    metadata={"phase": "replying"},
                )
            )

        async def _emit_audio_delta(delta_b64: str) -> None:
            await _emit_audio_started()
            self._record_thread_activity(thread_id)
            await self._dispatch_front_signal(
                FrontSignal(
                    name="assistant_audio_delta",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    user_text=user_text,
                    metadata={
                        "phase": "replying",
                        "chunk_bytes": len(str(delta_b64 or "")),
                    },
                )
            )

        async def _emit_audio_finished(played_any: bool) -> None:
            nonlocal audio_interrupted, finished_emitted
            if not played_any:
                if started_emitted:
                    audio_interrupted = True
                return
            if finished_emitted:
                return
            await _emit_audio_started()
            finished_emitted = True
            self._record_thread_activity(thread_id)
            await self._dispatch_front_signal(
                FrontSignal(
                    name="assistant_audio_finished",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    user_text=user_text,
                    metadata={"phase": "settling"},
                )
            )

        try:
            LOGGER.info(
                "Reply audio handler invoked: thread_id=%s turn_id=%s chars=%s",
                thread_id,
                turn_id,
                len(str(text or "")),
            )
            maybe_awaitable = handler(
                {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "user_text": user_text,
                    "text": str(text or ""),
                    "on_started": _emit_audio_started,
                    "on_audio_delta": _emit_audio_delta,
                    "on_finished": _emit_audio_finished,
                }
            )
            if isawaitable(maybe_awaitable):
                played_audio = bool(await maybe_awaitable)
            else:
                played_audio = bool(maybe_awaitable)
        except Exception as exc:
            await self._publish_turn_error(
                thread_id=thread_id,
                turn_id=turn_id,
                error=f"Reply audio failed: {exc}",
            )
            LOGGER.warning(
                "Reply audio handler failed: thread_id=%s turn_id=%s error=%s",
                thread_id,
                turn_id,
                exc,
            )
            return False

        played_audio = played_audio or started_emitted or finished_emitted
        LOGGER.info(
            "Reply audio handler completed: thread_id=%s turn_id=%s played=%s started=%s finished=%s interrupted=%s",
            thread_id,
            turn_id,
            played_audio,
            started_emitted,
            finished_emitted,
            audio_interrupted,
        )
        if played_audio and not started_emitted:
            await _emit_audio_started()
        if audio_interrupted:
            return played_audio
        if played_audio and not finished_emitted:
            await _emit_audio_finished(True)
        return played_audio
