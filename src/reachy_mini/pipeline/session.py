"""Long-lived SDK-first v4 runtime session."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from reachy_mini.action_runtime import ActionExecutor
from reachy_mini.action_runtime.library import (
    create_builtin_registry,
    create_live2d_registry,
)
from reachy_mini.action_runtime.registry import ActionRegistry
from reachy_mini.reachy_brain.agent import BrainAgent, ClientFactory
from reachy_mini.reachy_brain.config import AgentConfig, from_profile
from reachy_mini.robot_runtime.adapters.factory import (
    adapter_config_options,
    build_profile_adapters,
)
from reachy_mini.robot_runtime.config import RobotRuntimeConfig
from reachy_mini.robot_runtime.runtime import RobotRuntime
from reachy_mini.robot_runtime.tools import RobotIntentTools
from reachy_mini.runtime.live2d_avatar import (
    live2d_system_prompt,
    load_live2d_capabilities,
)

from .action_dispatcher import ActionDispatcher
from .brain_processor import BrainProcessor
from .frames import (
    AudioFrame,
    BrowserInputFrame,
    EmbodimentFrame,
    InterruptFrame,
    PipelineErrorFrame,
    SDKMessageFrame,
    SpeechActivityFrame,
    TranscriptionFrame,
    TTSStopFrame,
    VisionEventFrame,
    WorkerEventFrame,
)
from .output_bus import OutputBus, OutputSubscription
from .speech_presenter import SpeechPresenter
from .tts_kokoro import KokoroAdapter

LOGGER = logging.getLogger(__name__)

SURFACE_TASK_ID = "__surface__"
BRAIN_TURN_TIMEOUT_S = 45.0
BRAIN_STOP_TIMEOUT_S = 12.0


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
    """Multi-turn v4 session around ClaudeSDKClient / Pi RPC and ActionRuntime."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        registry: ActionRegistry,
        agent: Any,
        executor: ActionExecutor,
        speech: SpeechPresenter,
        tts: KokoroAdapter,
        actions: ActionDispatcher,
        clock: Clock | None = None,
        robot_runtime: RobotRuntime | None = None,
        robot_adapters: list[Any] | None = None,
        brain_backend: str = "claude_sdk",
        tool_bridge_server: Any | None = None,
    ) -> None:
        """Create a runtime session from prebuilt components."""
        self.config = config
        self.registry = registry
        self.agent = agent
        self.executor = executor
        self.speech = speech
        self.tts = tts
        self.actions = actions
        self.robot_runtime = robot_runtime
        self.robot_tools = RobotIntentTools(robot_runtime) if robot_runtime else None
        self._robot_adapters = list(robot_adapters or [])
        self._robot_adapters_registered = False
        self.brain_backend = (brain_backend or "claude_sdk").strip().lower()
        self.tool_bridge_server = tool_bridge_server
        self.clock = clock or _SystemClock()
        self.brain = BrainProcessor(agent)
        self.bus = OutputBus()
        if (
            self.robot_runtime is not None
            and self.robot_runtime.dependencies.publish_event is None
        ):
            self.robot_runtime.dependencies.publish_event = self.bus.publish
        if self.actions._publish is None:
            self.actions._publish = self._publish_action_frame
        self.pipecat: Any | None = None
        self._turn_workers: dict[str, set[str]] = {}
        self._started = False
        self._stopping = False
        self._surface_phase = "idle"
        self._active_turn_id = ""

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
        robot_runtime_config = RobotRuntimeConfig.from_records(
            _read_profile_records(profile_path)
        )
        robot_runtime = (
            RobotRuntime(config=robot_runtime_config)
            if robot_runtime_config.enabled
            else None
        )
        robot_tools = RobotIntentTools(robot_runtime) if robot_runtime else None
        live2d_capabilities = load_live2d_capabilities(profile_path)
        system_prompt_append = ""
        if live2d_capabilities is None:
            registry = create_builtin_registry()
            if robot_runtime_config.enabled:
                system_prompt_append = _robot_runtime_system_prompt()
        else:
            registry = create_live2d_registry(live2d_capabilities)
            if (
                robot_runtime_config.enabled
                and not robot_runtime_config.legacy_live2d_tools_enabled
            ):
                system_prompt_append = _robot_runtime_system_prompt()
            else:
                system_prompt_append = live2d_system_prompt(live2d_capabilities)
        mini = mini_factory(config) if mini_factory is not None else _NoopMini()
        adapter_build = build_profile_adapters(
            robot_runtime_config,
            live2d_capabilities=live2d_capabilities,
            mini=mini,
        )
        executor = ActionExecutor(registry=registry, mini=mini)
        actions = ActionDispatcher(executor)
        from reachy_mini.reachy_brain.pi_binary_brain import PiBinaryBrain
        from reachy_mini.reachy_brain.pi_tool_bridge import PiToolBridge
        from reachy_mini.reachy_brain.pi_tool_bridge_server import PiToolBridgeServer

        brain_backend = _resolve_brain_backend(config, overrides)
        use_pi = brain_backend == "pi_rpc"

        tool_bridge_server: PiToolBridgeServer | None = None
        if use_pi and robot_tools is not None and robot_runtime_config.intent_tools_enabled:
            bridge_host = os.environ.get("REACHY_RUNTIME_HOST", "127.0.0.1").strip() or "127.0.0.1"
            bridge_port = int(os.environ.get("REACHY_RUNTIME_PORT", "8787") or 8787)
            bridge_token = os.environ.get("REACHY_RUNTIME_TOKEN") or None
            tool_bridge_server = PiToolBridgeServer(
                PiToolBridge(robot_tools=robot_tools, token=bridge_token),
                host=bridge_host,
                port=bridge_port,
            )

        if use_pi:
            # Robot tools are registered in the TS extension; do not dual-mount MCP.
            agent = PiBinaryBrain(
                config=config,
                cwd=cwd,
                system_prompt_append=system_prompt_append,
                tool_bridge=tool_bridge_server,
                runtime_url=(
                    os.environ.get("REACHY_RUNTIME_URL")
                    or (
                        f"http://{tool_bridge_server.host}:{tool_bridge_server.port}"
                        if tool_bridge_server is not None
                        else "http://127.0.0.1:8787"
                    )
                ),
                runtime_token=os.environ.get("REACHY_RUNTIME_TOKEN") or None,
                pi_bin=os.environ.get("REACHY_PI_BIN") or None,
                extension_path=os.environ.get("REACHY_PI_EXT") or None,
            )
        else:
            agent = BrainAgent(
                config=config,
                registry=registry,
                run_action=actions.run_action,
                client_factory=client_factory,
                cwd=cwd,
                system_prompt_append=system_prompt_append,
                robot_tools=robot_tools if robot_runtime_config.intent_tools_enabled else None,
                action_tools_enabled=not (
                    robot_runtime_config.enabled
                    and not robot_runtime_config.legacy_live2d_tools_enabled
                ),
            )
        speech = SpeechPresenter(
            style={"voice": config.speech.voice, "speed": config.speech.speed}
        )
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
            robot_runtime=robot_runtime,
            robot_adapters=adapter_build.adapters,
            brain_backend=brain_backend,
            tool_bridge_server=tool_bridge_server,
        )
        if robot_tools is not None:
            session.robot_tools = robot_tools
        return session

    async def start(self) -> None:
        """Start the SDK client session."""
        if self._started:
            return
        self._started = True
        self._stopping = False
        if self.robot_runtime is not None:
            await self._register_robot_adapters()
            await self.robot_runtime.start()
        # Tool bridge before brain so Pi TS tools can reach RobotIntentTools.
        if self.tool_bridge_server is not None:
            await self.tool_bridge_server.start()
            from reachy_mini.reachy_brain.pi_binary_brain import PiBinaryBrain

            if isinstance(self.agent, PiBinaryBrain):
                self.agent.runtime_url = self.tool_bridge_server.base_url
        await self.agent.start()
        # Lazy import: pipecat pulls heavy optional deps (nltk/regex).
        from .pipecat_runtime import ReachyPipecatRuntime

        self.pipecat = ReachyPipecatRuntime(
            bus=self.bus,
            brain=self.brain,
            speech=self.speech,
            tts=self.tts,
            actions=self.actions,
            on_interrupt=self._handle_interrupt,
            on_sdk_message=self._handle_sdk_message_side_effects,
            on_brain_timeout=self._handle_brain_timeout,
            on_brain_error=self._handle_brain_exception,
            brain_turn_timeout_s=BRAIN_TURN_TIMEOUT_S,
            audio_in_sample_rate=int(
                getattr(self.config.speech_input, "sample_rate", 16000) or 16000
            ),
            audio_out_sample_rate=self.config.speech.sample_rate,
        )
        await self.pipecat.start()
        await self._set_surface("idle")

    async def stop(self) -> None:
        """Cancel running actions/tasks and shut down the session."""
        if not self._started:
            return
        self._stopping = True
        self.executor.cancel()
        if self.pipecat is not None:
            await self.pipecat.stop()
            self.pipecat = None
        await self._stop_sdk_tasks()
        await self.agent.stop()
        if self.tool_bridge_server is not None:
            await self.tool_bridge_server.stop()
        if self.robot_runtime is not None:
            await self.robot_runtime.stop()
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

    async def _register_robot_adapters(self) -> None:
        if self.robot_runtime is None or self._robot_adapters_registered:
            return
        for adapter in self._robot_adapters:
            if hasattr(adapter, "publish_frame") and getattr(adapter, "publish_frame") is None:
                setattr(adapter, "publish_frame", self.bus.publish)
            await self.robot_runtime.register_adapter(
                adapter,
                config=adapter_config_options(
                    self.robot_runtime.config,
                    adapter.adapter_id,
                ),
            )
        self._robot_adapters_registered = True

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
        turn_id = str(
            frame.payload.get("turn_id") or frame.session_id or self._make_turn_id()
        )
        await self._set_surface("replying")
        frame.payload["turn_id"] = turn_id
        previous_turn = self._active_turn_id
        self._active_turn_id = turn_id
        try:
            await self._submit_pipecat(frame)
            await self._maybe_set_idle()
        finally:
            self._active_turn_id = previous_turn
        return turn_id

    async def submit_audio_chunk(self, frame: AudioFrame) -> None:
        """Forward an audio frame to upstream consumers."""
        await self._submit_pipecat(frame, wait=False)

    async def submit_speech_activity(self, frame: SpeechActivityFrame) -> None:
        """Forward speech activity to BrainProcessor, surfacing barge-in interrupts."""
        if frame.state == "start":
            await self._set_surface("listening")
        elif frame.state == "end":
            await self._set_surface("listening_wait")
        await self._submit_pipecat(frame)

    async def submit_transcription(self, frame: TranscriptionFrame) -> None:
        """Push a final transcription through BrainProcessor and publish previews."""
        await self.bus.publish(frame)
        if not frame.is_final:
            await self._submit_pipecat(frame, wait=False)
            return
        await self._set_surface("replying")
        previous_turn = self._active_turn_id
        self._active_turn_id = frame.turn_id
        try:
            await self._submit_pipecat(frame)
            await self._maybe_set_idle()
        finally:
            self._active_turn_id = previous_turn

    async def submit_vision_event(self, frame: VisionEventFrame) -> None:
        """Push a vision event into Brain context."""
        await self.bus.publish(frame)
        await self._submit_pipecat(frame, wait=False)

    async def wait_for_turn_idle(
        self, turn_id: str, *, timeout: float | None = None
    ) -> None:
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
        try:
            outputs = await asyncio.wait_for(
                self.brain.process(frame),
                timeout=BRAIN_TURN_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            await self._reset_agent_after_brain_error()
            raise
        except asyncio.TimeoutError:
            reason = f"brain turn timed out after {BRAIN_TURN_TIMEOUT_S:.0f}s"
            LOGGER.warning("%s for turn %s", reason, turn_id)
            await self._publish_brain_error(reason, turn_id=turn_id)
            return
        except Exception as exc:
            reason = self._safe_brain_error_reason(exc)
            LOGGER.exception("Brain turn failed for %s: %s", turn_id, reason)
            await self._publish_brain_error(reason, turn_id=turn_id)
            return
        await self._handle_processor_outputs(outputs, turn_id=turn_id)

    async def _submit_pipecat(self, frame: object, *, wait: bool = True) -> None:
        if self.pipecat is None:
            await self._dispatch_brain(frame, turn_id=_turn_id_for_frame(frame))
            return
        await self.pipecat.submit(frame, wait=wait)

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

    async def _publish_action_frame(self, frame: object) -> None:
        """Publish action output from the action runtime."""
        await self.bus.publish(frame)

    async def _handle_sdk_message(self, frame: SDKMessageFrame) -> None:
        extra = await self._handle_sdk_message_side_effects(frame)
        await self.bus.publish(frame)
        for item in extra:
            await self.bus.publish(item)

    async def _handle_sdk_message_side_effects(
        self,
        frame: SDKMessageFrame,
    ) -> list[object]:
        extra: list[object] = []
        from reachy_mini.reachy_brain.pipecat_bridge import sdk_message_to_worker_event

        worker_frame = sdk_message_to_worker_event(frame)
        if worker_frame is not None:
            extra = await self._handle_worker_event(frame.turn_id, worker_frame, publish=False)
        return extra

    async def _handle_worker_event(
        self,
        turn_id: str,
        frame: WorkerEventFrame,
        *,
        publish: bool = True,
    ) -> list[object]:
        if turn_id and frame.event in {"started", "progress"}:
            self._turn_workers.setdefault(turn_id, set()).add(frame.task_id)
        await self.brain.process(frame)
        if frame.event in {"completed", "failed", "stopped", "cancelled"}:
            bucket = self._turn_workers.get(turn_id)
            if bucket is not None:
                bucket.discard(frame.task_id)
                if not bucket:
                    self._turn_workers.pop(turn_id, None)
        if publish:
            await self.bus.publish(frame)
            return []
        return [frame]

    async def _handle_interrupt(self, interrupt: InterruptFrame) -> None:
        if self.pipecat is None:
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

    async def _set_surface(self, phase: str) -> None:
        if phase == self._surface_phase:
            return
        self._surface_phase = phase
        ts_ms = int(self.clock.monotonic() * 1000)
        state = _surface_state_payload(phase)
        worker_frame = WorkerEventFrame(
            task_id=SURFACE_TASK_ID,
            event="state",
            payload={"kind": "surface_state", "state": state},
            ts_ms=ts_ms,
        )
        embodiment_frame = EmbodimentFrame(
            action="surface_state",
            target="all",
            turn_id=self._active_turn_id,
            payload=state,
            ts_ms=ts_ms,
        )
        await self.bus.publish(worker_frame)
        await self.bus.publish(embodiment_frame)

    async def _maybe_set_idle(self) -> None:
        if self._stopping:
            return
        if self._turn_workers:
            return
        await self._set_surface("idle")

    async def _publish_brain_error(self, reason: str, *, turn_id: str) -> None:
        await self._reset_agent_after_brain_error()
        await self.bus.publish(
            PipelineErrorFrame(
                component="brain",
                reason=reason,
                metadata={"turn_id": turn_id} if turn_id else {},
            )
        )
        await self._set_surface("idle")

    async def _handle_brain_timeout(self, turn_id: str) -> list[object]:
        reason = f"brain turn timed out after {BRAIN_TURN_TIMEOUT_S:.0f}s"
        LOGGER.warning("%s for turn %s", reason, turn_id)
        await self._reset_agent_after_brain_error()
        await self._set_surface("idle")
        return [
            PipelineErrorFrame(
                component="brain",
                reason=reason,
                metadata={"turn_id": turn_id} if turn_id else {},
            )
        ]

    async def _handle_brain_exception(self, exc: Exception) -> list[object]:
        reason = self._safe_brain_error_reason(exc)
        LOGGER.exception("Brain turn failed: %s", reason)
        await self._reset_agent_after_brain_error()
        await self._set_surface("idle")
        return [PipelineErrorFrame(component="brain", reason=reason)]

    async def _reset_agent_after_brain_error(self) -> None:
        try:
            await self.agent.reset(timeout_s=BRAIN_STOP_TIMEOUT_S)
        except Exception:
            LOGGER.exception("Failed to reset SDK client after brain error")

    def _safe_brain_error_reason(self, exc: Exception) -> str:
        text = str(exc).strip()
        api_key = self.config.model.api_key
        if api_key:
            text = text.replace(api_key, "[redacted]")
        if len(text) > 300:
            text = f"{text[:297]}..."
        if text:
            return f"{type(exc).__name__}: {text}"
        return type(exc).__name__

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


def _turn_id_for_frame(frame: object) -> str:
    if isinstance(frame, TranscriptionFrame):
        return frame.turn_id
    if isinstance(frame, BrowserInputFrame):
        return str(frame.payload.get("turn_id") or frame.session_id or "")
    if isinstance(frame, InterruptFrame):
        return frame.turn_id
    return ""


def _surface_state_payload(phase: str) -> dict[str, str]:
    states = {
        "idle": {"phase": "idle", "pose": "idle", "attention": "front"},
        "listening": {"phase": "listening", "pose": "listen", "attention": "front"},
        "listening_wait": {
            "phase": "listening_wait",
            "pose": "think",
            "attention": "front",
        },
        "replying": {"phase": "replying", "pose": "speak", "attention": "front"},
    }
    return states.get(
        phase,
        {"phase": phase, "pose": "neutral", "attention": "front"},
    )


def _robot_runtime_system_prompt() -> str:
    return (
        "Current embodiment mode: RobotRuntime.\n"
        "Use only the body-agnostic Robot Intent API for embodiment: "
        "emit_embodied_intent, query_robot_state, query_robot_trace, "
        "query_robot_metrics, query_robot_behavior_tree, "
        "query_robot_structured_log, request_robot_task, and cancel_robot_task.\n"
        "Do not call, mention, or invent concrete Live2D motion tools, "
        "Live2D expression tools, .motion3.json files, .exp3.json files, or raw "
        "motor commands. Express intent_type, affect, target, modalities, "
        "speech_relation, timing_anchor, intensity, priority, and constraints; "
        "RobotRuntime will resolve the correct body adapter and capability."
    )


def _resolve_brain_backend(
    config: AgentConfig,
    overrides: dict[str, Any] | None,
) -> str:
    """Resolve brain backend: override > env > profile extras > default claude_sdk."""
    if overrides:
        for key in ("brain.backend", "brain_backend"):
            if key in overrides and overrides[key]:
                return str(overrides[key]).strip().lower()
    env = os.environ.get("REACHY_BRAIN_BACKEND", "").strip().lower()
    if env:
        return env
    extras = getattr(config, "extras", {}) or {}
    for kind in ("brain", "agent"):
        records = extras.get(kind) or []
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                backend = record.get("backend") or record.get("brain_backend")
                if backend:
                    return str(backend).strip().lower()
    return "claude_sdk"


def _read_profile_records(profile_path: Path) -> list[dict[str, Any]]:
    config_path = profile_path if profile_path.is_file() else profile_path / "config.jsonl"
    if not config_path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in config_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            records.append(json.loads(text))
    return records
