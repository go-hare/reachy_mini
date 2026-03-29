"""Tests for the real brain-kernel-backed runtime runner."""

import asyncio
from pathlib import Path

from reachy_mini.affect import AffectState, AffectTurnResult, EmotionSignal, PADVector
from reachy_mini.core import BrainOutput, BrainOutputType, BrainResponse, TaskType
from reachy_mini.core.memory import MemoryView
from reachy_mini.runtime.config import load_profile_runtime_config
from reachy_mini.runtime.profile_loader import load_profile_bundle
from reachy_mini.runtime.scheduler import RuntimeScheduler
from reachy_mini.runtime.tool_loader import build_runtime_tool_bundle
from reachy_mini.runtime.tools import ReachyToolContext
from reachy_mini.front.events import FrontToolExecution, FrontUserTurnResult
from reachy_mini.runtime.camera_worker import ReactiveVisionEvent


def _write_profile(profile_root: Path) -> None:
    for filename, content in {
        "AGENTS.md": "保持真实",
        "USER.md": "用户偏好中文",
        "SOUL.md": "温柔、可靠",
        "TOOLS.md": "文本阶段先不要假装已经运行工具",
        "FRONT.md": "前台表达要自然、贴近、简洁。",
        "config.jsonl": (
            '{"kind":"front","mode":"text","style":"friendly_concise","history_limit":4}\n'
            '{"kind":"front_model","provider":"mock","model":"reachy_mini_front_mock","temperature":0.4}\n'
            '{"kind":"kernel_model","provider":"mock","model":"reachy_mini_kernel_mock","temperature":0.2}\n'
        ),
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


async def _collect_final_reply(runtime: RuntimeScheduler, *, thread_id: str, user_text: str) -> str:
    queue = runtime.subscribe_front_outputs()
    final_reply = ""
    try:
        await runtime.handle_user_text(
            thread_id=thread_id,
            session_id=thread_id,
            user_id="user",
            user_text=user_text,
        )
        await runtime.wait_for_thread_idle(thread_id)
        while not queue.empty():
                packet = queue.get_nowait()
                try:
                    if packet.thread_id == thread_id and packet.type == "front_final_done":
                        final_reply = str(packet.text or "").strip()
                finally:
                    queue.task_done()
        return final_reply
    finally:
        runtime.unsubscribe_front_outputs(queue)


def test_kernel_agent_runner_uses_brain_kernel_records(tmp_path: Path) -> None:
    """The stage-3 runner should persist front and brain records via BrainKernel."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        runtime = RuntimeScheduler.from_profile(
            profile=profile,
            config=config,
            enable_affect=False,
        )

        await runtime.start()
        try:
            reply = await _collect_final_reply(
                runtime,
                thread_id="cli:main",
                user_text="帮我看看日志",
            )
        finally:
            await runtime.stop()

        front_path = profile_root / "session" / "cli_main" / "front.jsonl"
        brain_path = profile_root / "session" / "cli_main" / "brain.jsonl"
        front_text = front_path.read_text(encoding="utf-8")
        brain_text = brain_path.read_text(encoding="utf-8")

        assert reply == "需要先查看和“帮我看看日志”相关的文件或日志，确认后才能给你准确结论。"
        assert '"front_reply": "我先帮你看一下帮我看看日志，看完马上回来跟你说。"' in front_text
        assert '"role": "user"' in brain_text
        assert '"role": "assistant"' in brain_text

    asyncio.run(_exercise())


def test_kernel_agent_runner_handles_multiple_turns_in_one_resident_runtime(
    tmp_path: Path,
) -> None:
    """One started runtime should handle consecutive turns without restarting the kernel."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        runtime = RuntimeScheduler.from_profile(
            profile=profile,
            config=config,
            enable_affect=False,
        )

        await runtime.start()
        try:
            first_reply = await _collect_final_reply(
                runtime,
                thread_id="cli:main",
                user_text="先帮我看日志",
            )
            assert runtime.kernel.is_running

            second_reply = await _collect_final_reply(
                runtime,
                thread_id="cli:main",
                user_text="再总结一下刚才看到的情况",
            )
            assert runtime.kernel.is_running
        finally:
            await runtime.stop()

        brain_path = profile_root / "session" / "cli_main" / "brain.jsonl"
        brain_text = brain_path.read_text(encoding="utf-8")

        assert first_reply
        assert second_reply
        assert brain_text.count('"role": "user"') >= 2
        assert brain_text.count('"role": "assistant"') >= 2


class FakeFront:
    """Test double for front reply/present calls."""

    def __init__(self) -> None:
        self.reply_calls: list[dict[str, object]] = []
        self.present_calls: list[dict[str, object]] = []
        self.signal_calls: list[object] = []
        self.tool_runs: list[dict[str, object]] = []
        self.tools = [self.FakeExpressiveTool(self.tool_runs)]

    class FakeExpressiveTool:
        def __init__(self, sink: list[dict[str, object]]) -> None:
            self.name = "do_nothing"
            self._sink = sink

        def validate_params(self, params: dict[str, object]) -> list[str]:
            _ = params
            return []

        async def execute(self, **kwargs):
            self._sink.append(dict(kwargs))
            return "front tool executed"

    def get_tool(self, name: str):
        for tool in self.tools:
            if getattr(tool, "name", "") == name:
                return tool
        return None

    async def reply(self, **kwargs):
        self.reply_calls.append(kwargs)
        return "front hint"

    async def handle_user_turn(self, **kwargs):
        reply_text = await self.reply(**kwargs)
        return FrontUserTurnResult(
            reply_text=reply_text,
            completes_turn=False,
        )

    async def present(self, **kwargs):
        self.present_calls.append(kwargs)
        return "front final"

    async def execute_tool_calls(self, calls):
        results: list[FrontToolExecution] = []
        for call in list(calls):
            tool_name = str(getattr(call, "tool_name", "") or "")
            arguments = dict(getattr(call, "arguments", {}) or {})
            reason = str(getattr(call, "reason", "") or "")
            tool = self.get_tool(tool_name)
            if tool is None:
                results.append(
                    FrontToolExecution(
                        tool_name=tool_name,
                        arguments=arguments,
                        reason=reason,
                        success=False,
                        result="tool not found",
                    )
                )
                continue
            execution = await tool.execute(**arguments)
            results.append(
                FrontToolExecution(
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=reason,
                    success=True,
                    result=str(execution or ""),
                )
            )
        return results

    async def handle_signal(self, signal):
        self.signal_calls.append(signal)
        signal_name = getattr(signal, "name", "")
        lifecycle_state = "idle" if signal_name == "idle_entered" else ""
        tool_calls = (
            [
                {
                    "tool_name": "do_nothing",
                    "arguments": {"reason": "idle hold"},
                    "reason": "idle hold",
                }
            ]
            if signal_name == "idle_entered"
            else []
        )
        return {
            "signal_name": signal_name,
            "lifecycle_state": lifecycle_state,
            "tool_calls": tool_calls,
        }


class FakeMemoryStore:
    """Small memory-store stub for the runner."""

    def build_memory_view(
        self,
        conversation_id: str,
        agent_id: str,
        query: str,
        limit: int = 6,
    ) -> MemoryView:
        _ = conversation_id
        _ = agent_id
        _ = query
        _ = limit
        return MemoryView()


class FakeKernel:
    """Resident-kernel stub that emits one queued response."""

    def __init__(self) -> None:
        self.agent_id = "demo"
        self.memory_store = FakeMemoryStore()
        self.front_events: list[dict[str, object]] = []
        self.user_inputs: list[dict[str, object]] = []
        self._output_queue: asyncio.Queue[BrainOutput] = asyncio.Queue()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        await self._output_queue.put(
            BrainOutput(event_id="stop", type=BrainOutputType.stopped)
        )

    async def publish_front_event(self, **kwargs):
        self.front_events.append(kwargs)
        return kwargs

    async def publish_user_input(self, **kwargs):
        self.user_inputs.append(kwargs)
        await self._output_queue.put(
            BrainOutput(
                event_id=str(kwargs.get("event_id", "")),
                type=BrainOutputType.response,
                response=BrainResponse(task_type=TaskType.simple, reply="kernel raw"),
            )
        )
        return str(kwargs.get("event_id", ""))

    async def recv_output(self) -> BrainOutput:
        return await self._output_queue.get()


class FakeAffectRuntime:
    """Affect stub that returns a deterministic state and emotion."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.state = AffectState(
            current_pad=PADVector(pleasure=-0.3, arousal=0.4, dominance=0.1),
            vitality=0.62,
            pressure=0.48,
            updated_at="2026-03-26T10:00:00",
        )
        self.emotion_signal = EmotionSignal(
            primary_emotion="anxious",
            intensity=0.7,
            confidence=0.9,
            support_need="focused",
            wants_action=True,
            trigger_text="帮我看看日志",
        )

    def evolve(self, *, user_text: str) -> AffectTurnResult:
        self.calls.append(user_text)
        return AffectTurnResult(
            previous_state=self.state,
            state=self.state,
            user_pad=PADVector(),
            delta_pad=PADVector(),
            pressure_delta=0.0,
            emotion_signal=self.emotion_signal,
        )


class FakeReactiveVisionCameraWorker:
    """Camera-worker stub exposing the new reactive-vision listener API."""

    def __init__(self) -> None:
        self.listeners: list[object] = []

    def add_reactive_vision_listener(self, listener) -> None:
        self.listeners.append(listener)

    def remove_reactive_vision_listener(self, listener) -> None:
        self.listeners = [item for item in self.listeners if item is not listener]

    def emit(self, event: ReactiveVisionEvent) -> None:
        for listener in list(self.listeners):
            listener(event)


class FakeVisionFront(FakeFront):
    """Front stub that reacts to bridged reactive-vision signals."""

    def __init__(self) -> None:
        super().__init__()
        self.tools = [self.FakeMoveHeadTool(self.tool_runs), self.FakeExpressiveTool(self.tool_runs)]

    class FakeMoveHeadTool:
        def __init__(self, sink: list[dict[str, object]]) -> None:
            self.name = "move_head"
            self._sink = sink

        def validate_params(self, params: dict[str, object]) -> list[str]:
            _ = params
            return []

        async def execute(self, **kwargs):
            self._sink.append(dict(kwargs))
            return "front move executed"

    async def handle_signal(self, signal):
        signal_name = getattr(signal, "name", "")
        if signal_name == "vision_attention_updated":
            self.signal_calls.append(signal)
            direction = str(getattr(signal, "metadata", {}).get("direction", "front") or "front")
            return {
                "signal_name": signal_name,
                "lifecycle_state": "attending",
                "surface_patch": {
                    "phase": "attending",
                    "source_signal": signal_name,
                },
                "tool_calls": [
                    {
                        "tool_name": "move_head",
                        "arguments": {"direction": direction},
                        "reason": "align gaze with reactive vision",
                    }
                ],
            }
        return await super().handle_signal(signal)


def test_kernel_agent_runner_passes_affect_and_companion_through_front(tmp_path: Path) -> None:
    """Kernel mode should pass affect/emotion into front reply and present."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeFront()
        kernel = FakeKernel()
        affect_runtime = FakeAffectRuntime()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=affect_runtime,
            front_style=config.front_style,
        )

        latest_decision = None
        await runtime.start()
        try:
            reply = await _collect_final_reply(
                runtime,
                thread_id="cli:main",
                user_text="帮我看看日志",
            )
            latest_decision = runtime.get_thread_front_decision("cli:main")
        finally:
            await runtime.stop()

        assert reply == "front final"
        assert affect_runtime.calls == ["帮我看看日志"]
        assert front.reply_calls[0]["emotion_signal"] == affect_runtime.emotion_signal
        assert kernel.user_inputs[0]["latest_front_reply"] == "front hint"
        assert front.present_calls[0]["affect_state"] == affect_runtime.state
        assert front.present_calls[0]["emotion_signal"] == affect_runtime.emotion_signal
        assert front.present_calls[0]["companion_intent"] is not None
        assert front.present_calls[0]["surface_expression"] is not None
        assert kernel.front_events[0]["front_event"]["metadata"]["emotion_primary"] == "anxious"
        assert kernel.front_events[1]["front_event"]["metadata"]["kernel_output"] == "kernel raw"
        assert kernel.front_events[1]["front_event"]["metadata"]["mode"] == "focused"
        assert [getattr(signal, "name", "") for signal in front.signal_calls] == [
            "turn_started",
            "listening_entered",
            "kernel_output_ready",
            "settling_entered",
            "idle_entered",
        ]
        assert latest_decision is not None
        assert latest_decision["signal_name"] == "idle_entered"
        assert latest_decision["lifecycle_state"] == "idle"
        assert latest_decision["tool_calls"]
        assert latest_decision["tool_calls"][0]["tool_name"] == "do_nothing"
        assert front.tool_runs == [{"reason": "idle hold"}]

    asyncio.run(_exercise())


def test_runtime_scheduler_assigns_expressive_tools_to_front_only(tmp_path: Path) -> None:
    """Runtime construction should keep expressive tools off the kernel tool plane."""
    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile(profile_root)

    profile = load_profile_bundle(profile_root)
    config = load_profile_runtime_config(profile)
    tool_bundle = build_runtime_tool_bundle(profile)
    runtime = RuntimeScheduler.from_profile(
        profile=profile,
        config=config,
        enable_affect=False,
    )

    kernel_tool_names = [
        str(getattr(tool, "name", "") or "").strip() for tool in tool_bundle.kernel_tools
    ]

    assert "write_file" in kernel_tool_names
    assert "move_head" not in kernel_tool_names
    assert "play_emotion" not in kernel_tool_names
    assert "move_head" in runtime.front.tool_names
    assert "play_emotion" in runtime.front.tool_names
    assert "camera" in runtime.front.tool_names


def test_runtime_scheduler_publishes_front_tool_result_packets(tmp_path: Path) -> None:
    """Front decision tool execution should be surfaced as runtime output packets."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeFront()
        kernel = FakeKernel()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=None,
            front_style=config.front_style,
        )

        queue = runtime.subscribe_front_outputs()
        try:
            await runtime.start()
            await runtime.handle_user_text(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="帮我看看日志",
            )
            await runtime.wait_for_thread_idle("cli:main")

            payloads: list[dict[str, object]] = []
            while not queue.empty():
                packet = queue.get_nowait()
                try:
                    if packet.type == "front_tool_result" and packet.payload is not None:
                        payloads.append(dict(packet.payload))
                finally:
                    queue.task_done()
        finally:
            runtime.unsubscribe_front_outputs(queue)
            await runtime.stop()

        assert payloads
        assert payloads[-1]["tool_name"] == "do_nothing"
        assert payloads[-1]["success"] is True
        assert payloads[-1]["result"] == "front tool executed"

    asyncio.run(_exercise())


def test_runtime_scheduler_tracks_non_surfaced_front_decisions(tmp_path: Path) -> None:
    """Signal-only front reactions should still update the latest internal front decision."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeFront()
        kernel = FakeKernel()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=None,
            front_style=config.front_style,
        )

        await runtime.start()
        try:
            await runtime.handle_user_speech_started(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="喂",
            )
            latest_decision = runtime.get_thread_front_decision("cli:main")
            surface_state = runtime.get_thread_surface_state("cli:main")
        finally:
            await runtime.stop()

        assert latest_decision is not None
        assert latest_decision["signal_name"] == "user_speech_started"
        assert latest_decision["lifecycle_state"] == "listening"
        assert latest_decision["surface_patch"]["phase"] == "listening"
        assert latest_decision["surface_patch"]["source_signal"] == "user_speech_started"
        assert surface_state is not None
        assert surface_state["phase"] == "listening"
        assert surface_state["thread_id"] == "cli:main"

    asyncio.run(_exercise())


def test_runtime_scheduler_bridges_reactive_vision_attention_into_front(tmp_path: Path) -> None:
    """Reactive-vision attention events should bridge into the front signal path."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeVisionFront()
        kernel = FakeKernel()
        camera_worker = FakeReactiveVisionCameraWorker()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=None,
            front_style=config.front_style,
            runtime_tool_context=ReachyToolContext(camera_worker=camera_worker),
        )

        await runtime.start()
        try:
            camera_worker.emit(
                ReactiveVisionEvent(
                    name="attention_acquired",
                    metadata={
                        "source": "reactive_vision",
                        "direction": "left",
                        "tracking_enabled": True,
                    },
                )
            )
            await asyncio.sleep(0.05)

            latest_decision = runtime.get_thread_front_decision("app:main")
            surface_state = runtime.get_thread_surface_state("app:main")
        finally:
            await runtime.stop()

        assert [getattr(signal, "name", "") for signal in front.signal_calls] == [
            "vision_attention_updated"
        ]
        assert latest_decision is not None
        assert latest_decision["signal_name"] == "vision_attention_updated"
        assert latest_decision["lifecycle_state"] == "attending"
        assert latest_decision["surface_patch"]["phase"] == "attending"
        assert front.tool_runs == [{"direction": "left"}]
        assert surface_state is not None
        assert surface_state["phase"] == "attending"
        assert camera_worker.listeners == []

    asyncio.run(_exercise())


def test_runtime_scheduler_ignores_reactive_vision_while_listening(tmp_path: Path) -> None:
    """Reactive-vision attention should not override an active listening phase."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeVisionFront()
        kernel = FakeKernel()
        camera_worker = FakeReactiveVisionCameraWorker()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=None,
            front_style=config.front_style,
            runtime_tool_context=ReachyToolContext(camera_worker=camera_worker),
        )

        await runtime.start()
        try:
            await runtime.handle_user_speech_started(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="喂",
            )
            camera_worker.emit(
                ReactiveVisionEvent(
                    name="attention_acquired",
                    metadata={"source": "reactive_vision", "direction": "right"},
                )
            )
            await asyncio.sleep(0.05)

            latest_decision = runtime.get_thread_front_decision("cli:main")
            surface_state = runtime.get_thread_surface_state("cli:main")
        finally:
            await runtime.stop()

        assert [getattr(signal, "name", "") for signal in front.signal_calls] == [
            "user_speech_started"
        ]
        assert latest_decision is not None
        assert latest_decision["signal_name"] == "user_speech_started"
        assert latest_decision["lifecycle_state"] == "listening"
        assert surface_state is not None
        assert surface_state["phase"] == "listening"
        assert front.tool_runs == []

    asyncio.run(_exercise())


def test_runtime_scheduler_emits_assistant_audio_lifecycle_signals(tmp_path: Path) -> None:
    """Reply-audio playback should surface started/delta/finished lifecycle signals."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeFront()
        kernel = FakeKernel()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=None,
            front_style=config.front_style,
        )

        async def final_reply_handler(payload: dict[str, object]) -> bool:
            on_started = payload.get("on_started")
            on_audio_delta = payload.get("on_audio_delta")
            on_finished = payload.get("on_finished")
            if callable(on_started):
                await on_started()
            if callable(on_audio_delta):
                await on_audio_delta("demo-delta")
            if callable(on_finished):
                await on_finished(True)
            return True

        await runtime.start()
        try:
            await runtime.handle_user_text(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="帮我看看日志",
                final_reply_handler=final_reply_handler,
            )
            await runtime.wait_for_thread_idle("cli:main")
        finally:
            await runtime.stop()

        signal_names = [getattr(signal, "name", "") for signal in front.signal_calls]
        assert "assistant_audio_started" in signal_names
        assert "assistant_audio_delta" in signal_names
        assert "assistant_audio_finished" in signal_names
        assert signal_names.index("assistant_audio_started") < signal_names.index("assistant_audio_finished")
        assert signal_names.index("assistant_audio_finished") < signal_names.index("settling_entered")

    asyncio.run(_exercise())


def test_runtime_scheduler_reuses_front_reply_for_audio_when_kernel_returns_none(
    tmp_path: Path,
) -> None:
    """When the kernel decides there is no task, the front reply should still become the final spoken reply."""

    class FakeNoneKernel(FakeKernel):
        async def publish_user_input(self, **kwargs):
            self.user_inputs.append(kwargs)
            await self._output_queue.put(
                BrainOutput(
                    event_id=str(kwargs.get("event_id", "")),
                    type=BrainOutputType.response,
                    response=BrainResponse(task_type=TaskType.none, reply=""),
                )
            )
            return str(kwargs.get("event_id", ""))

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeFront()
        kernel = FakeNoneKernel()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=None,
            front_style=config.front_style,
        )

        spoken_texts: list[str] = []
        queue = runtime.subscribe_front_outputs()

        async def final_reply_handler(payload: dict[str, object]) -> bool:
            spoken_texts.append(str(payload.get("text", "") or ""))
            return True

        await runtime.start()
        try:
            await runtime.handle_user_text(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="帮我看看日志",
                final_reply_handler=final_reply_handler,
            )
            await runtime.wait_for_thread_idle("cli:main")

            final_reply = ""
            while not queue.empty():
                packet = queue.get_nowait()
                try:
                    if packet.thread_id == "cli:main" and packet.type == "front_final_done":
                        final_reply = str(packet.text or "")
                finally:
                    queue.task_done()
        finally:
            runtime.unsubscribe_front_outputs(queue)
            await runtime.stop()

        assert final_reply == "front hint"
        assert spoken_texts == ["front hint"]

    asyncio.run(_exercise())


def test_runtime_scheduler_emits_idle_tick_signals_while_thread_stays_idle(
    tmp_path: Path,
) -> None:
    """Idle threads should keep producing idle_tick signals on a cooldown."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeFront()
        kernel = FakeKernel()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=None,
            front_style=config.front_style,
            idle_tick_interval_s=0.02,
        )

        await runtime.start()
        try:
            await runtime.handle_user_text(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="帮我看看日志",
            )
            await runtime.wait_for_thread_idle("cli:main")
            await asyncio.sleep(0.05)
        finally:
            await runtime.stop()

        signal_names = [getattr(signal, "name", "") for signal in front.signal_calls]
        assert signal_names.count("idle_tick") >= 1

    asyncio.run(_exercise())


def test_runtime_scheduler_accepts_user_speech_lifecycle_signals(tmp_path: Path) -> None:
    """User speech lifecycle hooks should reach front and surface state before text commit."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeFront()
        kernel = FakeKernel()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=None,
            front_style=config.front_style,
            idle_tick_interval_s=0.02,
        )

        surface_states: list[dict[str, object]] = []
        latest_surface_state = None

        async def _surface_state_handler(state: dict[str, object]) -> None:
            surface_states.append(dict(state))

        await runtime.start()
        try:
            await runtime.handle_user_speech_started(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="我先说一下",
                surface_state_handler=_surface_state_handler,
            )
            await runtime.handle_user_speech_stopped(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="我先说一下",
                surface_state_handler=_surface_state_handler,
            )
            await asyncio.sleep(0.05)
            latest_surface_state = runtime.get_thread_surface_state("cli:main")
        finally:
            await runtime.stop()

        signal_names = [getattr(signal, "name", "") for signal in front.signal_calls]
        assert "user_speech_started" in signal_names
        assert "user_speech_stopped" in signal_names
        assert signal_names.count("idle_tick") >= 1
        assert surface_states[0]["phase"] == "listening"
        assert surface_states[1]["phase"] == "listening_wait"
        assert latest_surface_state is not None
        assert latest_surface_state["phase"] == "listening_wait"

    asyncio.run(_exercise())


def test_runtime_scheduler_interrupts_reply_audio_when_user_speech_starts(
    tmp_path: Path,
) -> None:
    """User speech should interrupt reply audio and prevent the old turn from re-entering settling."""

    class FakeInterruptibleReplyAudioService:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.interrupted = asyncio.Event()
            self.interrupt_calls = 0

        async def speak_text(
            self,
            text: str,
            *,
            on_started=None,
            on_audio_delta=None,
            on_finished=None,
        ) -> bool:
            _ = text
            if on_started is not None:
                await on_started()
            self.started.set()
            if on_audio_delta is not None:
                await on_audio_delta("demo-delta")
            await self.interrupted.wait()
            if on_finished is not None:
                await on_finished(False)
            return True

        async def interrupt_playback(self) -> bool:
            if self.interrupted.is_set():
                return False
            self.interrupt_calls += 1
            self.interrupted.set()
            await asyncio.sleep(0)
            return True

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeFront()
        kernel = FakeKernel()
        reply_audio_service = FakeInterruptibleReplyAudioService()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=None,
            front_style=config.front_style,
            idle_tick_interval_s=0.0,
            speech_interrupt_grace_s=0.02,
            runtime_tool_context=ReachyToolContext(
                reachy_mini=object(),
                reply_audio_service=reply_audio_service,
            ),
        )

        async def final_reply_handler(payload: dict[str, object]) -> bool:
            return await reply_audio_service.speak_text(
                str(payload.get("text", "") or ""),
                on_started=payload.get("on_started"),
                on_audio_delta=payload.get("on_audio_delta"),
                on_finished=payload.get("on_finished"),
            )

        await runtime.start()
        try:
            turn_task = asyncio.create_task(
                runtime.handle_user_text(
                    thread_id="cli:main",
                    session_id="cli:main",
                    user_id="user",
                    user_text="帮我看看日志",
                    final_reply_handler=final_reply_handler,
                )
            )
            await reply_audio_service.started.wait()
            await runtime.handle_user_speech_started(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="先别说了",
            )
            await turn_task
            await runtime.wait_for_thread_idle("cli:main")
            latest_surface_state = runtime.get_thread_surface_state("cli:main")
        finally:
            await runtime.stop()

        signal_names = [getattr(signal, "name", "") for signal in front.signal_calls]
        assert reply_audio_service.interrupt_calls == 1
        assert "assistant_audio_started" in signal_names
        assert "assistant_audio_finished" not in signal_names
        assert "settling_entered" not in signal_names
        assert latest_surface_state is not None
        assert latest_surface_state["phase"] == "listening"

    asyncio.run(_exercise())


def test_runtime_scheduler_keeps_reply_audio_when_user_speech_stops_within_grace(
    tmp_path: Path,
) -> None:
    """Brief speech-start blips should not interrupt reply audio playback."""

    class FakeGracePeriodReplyAudioService:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.finished = asyncio.Event()
            self.interrupted = asyncio.Event()
            self.interrupt_calls = 0

        async def speak_text(
            self,
            text: str,
            *,
            on_started=None,
            on_audio_delta=None,
            on_finished=None,
        ) -> bool:
            _ = text
            if on_started is not None:
                await on_started()
            self.started.set()
            if on_audio_delta is not None:
                await on_audio_delta("demo-delta")
            try:
                await asyncio.wait_for(self.interrupted.wait(), timeout=0.08)
            except asyncio.TimeoutError:
                pass
            if on_finished is not None:
                await on_finished(not self.interrupted.is_set())
            self.finished.set()
            return True

        async def interrupt_playback(self) -> bool:
            if self.interrupted.is_set():
                return False
            self.interrupt_calls += 1
            self.interrupted.set()
            await asyncio.sleep(0)
            return True

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        front = FakeFront()
        kernel = FakeKernel()
        reply_audio_service = FakeGracePeriodReplyAudioService()
        runtime = RuntimeScheduler(
            profile_root=profile.root,
            front=front,
            kernel=kernel,
            affect_runtime=None,
            front_style=config.front_style,
            idle_tick_interval_s=0.0,
            speech_interrupt_grace_s=0.05,
            runtime_tool_context=ReachyToolContext(
                reachy_mini=object(),
                reply_audio_service=reply_audio_service,
            ),
        )

        async def final_reply_handler(payload: dict[str, object]) -> bool:
            return await reply_audio_service.speak_text(
                str(payload.get("text", "") or ""),
                on_started=payload.get("on_started"),
                on_audio_delta=payload.get("on_audio_delta"),
                on_finished=payload.get("on_finished"),
            )

        await runtime.start()
        try:
            turn_task = asyncio.create_task(
                runtime.handle_user_text(
                    thread_id="cli:main",
                    session_id="cli:main",
                    user_id="user",
                    user_text="帮我看看日志",
                    final_reply_handler=final_reply_handler,
                )
            )
            await reply_audio_service.started.wait()
            await runtime.handle_user_speech_started(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="欸",
            )
            await asyncio.sleep(0.01)
            await runtime.handle_user_speech_stopped(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="",
            )
            await turn_task
            await runtime.wait_for_thread_idle("cli:main")
            latest_surface_state = runtime.get_thread_surface_state("cli:main")
        finally:
            await runtime.stop()

        signal_names = [getattr(signal, "name", "") for signal in front.signal_calls]
        assert reply_audio_service.interrupt_calls == 0
        assert "assistant_audio_started" in signal_names
        assert "assistant_audio_finished" in signal_names
        assert "settling_entered" in signal_names
        assert latest_surface_state is not None
        assert latest_surface_state["phase"] == "idle"

    asyncio.run(_exercise())
