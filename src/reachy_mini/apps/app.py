from __future__ import annotations

"""Reachy Mini application base classes and helpers."""

import asyncio
import importlib
import logging
import threading
import time
import traceback
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .inputs import (
    handle_runtime_websocket_payload,
)
from .inputs.runtime_dispatch import dispatch_user_speech_event, dispatch_user_turn
from .inputs.speech import (
    BrowserAudioChunkEvent,
    BrowserAudioStopEvent,
    decode_browser_audio_chunk,
    UserSpeechPartialEvent,
    UserSpeechStartedEvent,
    UserSpeechStoppedEvent,
)
from .inputs.text import UserTextEvent
from .runtime_host import AppRuntimeHostAdapter

if TYPE_CHECKING:
    from reachy_mini.reachy_mini import ReachyMini
    from reachy_mini.runtime.scheduler import RuntimeScheduler
    from reachy_mini.runtime.tools import ReachyToolContext


class ChatRequest(BaseModel):
    """One user message sent to the resident runtime."""

    message: str = Field(min_length=1)
    thread_id: str = "app:main"
    session_id: str | None = None
    user_id: str = "user"


class ChatResponse(BaseModel):
    """Final reply returned from the resident runtime."""

    thread_id: str
    reply: str
    error: str = ""
    surface_state: dict[str, Any] | None = None
    front_decision: dict[str, Any] | None = None
    front_tool_results: list[dict[str, Any]] = Field(default_factory=list)


class RuntimeStatusResponse(BaseModel):
    """Small readiness payload for the resident runtime."""

    ready: bool
    profile_root: str
    speech_input_enabled: bool = False


class ReachyMiniApp:
    """Base class for Reachy Mini applications."""

    custom_app_url: str | None = None
    dont_start_webserver: bool = False
    request_media_backend: str | None = None
    profile_root_relative_path: str | None = None
    runtime_request_timeout: float = 120.0

    def __init__(self, running_on_wireless: bool = False) -> None:
        """Initialize the Reachy Mini app."""
        self.stop_event = threading.Event()
        self.error: str = ""
        self.logger = logging.getLogger("reachy_mini.app")

        # Detect if daemon is available on localhost
        # If yes, use localhost connection. If no, use multicast scouting for remote daemon.
        self.daemon_on_localhost = self._check_daemon_on_localhost()
        self.logger.info(f"Daemon on localhost: {self.daemon_on_localhost}")

        # Media backend is now auto-detected by ReachyMini, just use "default"
        self.media_backend = (
            self.request_media_backend
            if self.request_media_backend is not None
            else "default"
        )
        self.profile_root = self.resolve_profile_root()
        self.runtime_host_adapter = AppRuntimeHostAdapter(
            profile_root=self.profile_root,
            logger=self.logger,
        )
        self.runtime: RuntimeScheduler | None = None
        self.runtime_config: Any | None = None
        self.runtime_loop: asyncio.AbstractEventLoop | None = None
        self.runtime_ready = threading.Event()
        self.runtime_tool_context: Any | None = None
        self.runtime_microphone_bridge: Any | None = None
        self._runtime_speech_input_block_until: float = 0.0
        self._runtime_browser_speech_sessions: dict[str, Any] = {}

        self.settings_app: FastAPI | None = None
        if self.custom_app_url is not None and not self.dont_start_webserver:
            self.settings_app = FastAPI()

            static_dir = self._get_instance_path().parent / "static"
            if static_dir.exists():
                self.settings_app.mount(
                    "/static", StaticFiles(directory=static_dir), name="static"
                )

                index_file = static_dir / "index.html"
                if index_file.exists():

                    @self.settings_app.get("/")
                    async def index() -> FileResponse:
                        """Serve the settings app index page."""
                        return FileResponse(index_file)

            if self.profile_root is not None:
                self._mount_runtime_socket()

    @staticmethod
    def _check_daemon_on_localhost(port: int = 8000, timeout: float = 0.5) -> bool:
        """Check if daemon is reachable on localhost.

        Args:
            port: Port to check (default: 8000)
            timeout: Connection timeout in seconds

        Returns:
            True if daemon responds on localhost, False otherwise

        """
        import socket

        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def wrapped_run(self, *args: Any, **kwargs: Any) -> None:
        """Wrap the run method with Reachy Mini context management."""
        settings_app_t: threading.Thread | None = None
        if self.settings_app is not None:
            import uvicorn

            assert self.custom_app_url is not None
            url = urlparse(self.custom_app_url)
            assert url.hostname is not None and url.port is not None

            config = uvicorn.Config(
                self.settings_app,
                host=url.hostname,
                port=url.port,
            )
            server = uvicorn.Server(config)

            def _server_run() -> None:
                """Run the settings FastAPI app."""
                t = threading.Thread(target=server.run)
                t.start()
                self.stop_event.wait()
                server.should_exit = True
                t.join()

            settings_app_t = threading.Thread(target=_server_run)
            settings_app_t.start()

        try:
            self.logger.info("Starting Reachy Mini app...")
            self.logger.info(f"Using media backend: {self.media_backend}")
            self.logger.info(f"Daemon on localhost: {self.daemon_on_localhost}")
            from reachy_mini.reachy_mini import ReachyMini

            # Force the connection mode based on daemon location detection
            connection_mode: Literal["localhost_only", "network"] = (
                "localhost_only" if self.daemon_on_localhost else "network"
            )

            with ReachyMini(
                media_backend=self.media_backend,
                connection_mode=connection_mode,
                *args,
                **kwargs,  # type: ignore
            ) as reachy_mini:
                self.run(reachy_mini, self.stop_event)
        except Exception:
            self.error = traceback.format_exc()
            raise
        finally:
            if settings_app_t is not None:
                self.stop_event.set()
                settings_app_t.join()

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run the main logic of the app.

        Args:
            reachy_mini (ReachyMini): The Reachy Mini instance to interact with.
            stop_event (threading.Event): An event that can be set to stop the app gracefully.

        """
        if self.profile_root is None:
            raise NotImplementedError(
                "App subclasses must implement run() or declare "
                "profile_root_relative_path to use the resident runtime."
            )

        self.runtime_tool_context = self.build_runtime_tool_context(reachy_mini)
        try:
            self.run_resident_runtime(stop_event)
        finally:
            self.cleanup_runtime_tool_context(self.runtime_tool_context)
            self.runtime_tool_context = None

    def build_runtime_tool_context(
        self,
        reachy_mini: ReachyMini | Any,
    ) -> "ReachyToolContext | None":
        """Build optional runtime tool dependencies from the running app instance."""
        return self.runtime_host_adapter.build_runtime_tool_context(reachy_mini)

    def cleanup_runtime_tool_context(self, context: Any | None) -> None:
        """Stop runtime-managed helper resources."""
        self.runtime_host_adapter.cleanup_runtime_tool_context(context)

    def feed_runtime_audio_delta(self, delta_b64: str) -> bool:
        """Feed one assistant audio delta into the runtime head wobbler."""
        return self.runtime_host_adapter.feed_runtime_audio_delta(
            self.runtime_tool_context,
            delta_b64,
        )

    def reset_runtime_audio_motion(self) -> bool:
        """Reset queued speech-motion audio state for the resident runtime."""
        return self.runtime_host_adapter.reset_runtime_audio_motion(self.runtime_tool_context)

    async def play_runtime_reply_audio(self, payload: dict[str, Any]) -> bool:
        """Synthesize and play one final runtime reply when speech output is configured."""
        cooldown_s = self._get_runtime_speech_input_playback_cooldown_s()
        if cooldown_s > 0.0:
            self._runtime_speech_input_block_until = max(
                self._runtime_speech_input_block_until,
                time.monotonic() + cooldown_s,
            )
        try:
            return await self.runtime_host_adapter.play_runtime_reply_audio(
                self.runtime_tool_context,
                payload,
            )
        finally:
            if cooldown_s > 0.0:
                self._runtime_speech_input_block_until = max(
                    self._runtime_speech_input_block_until,
                    time.monotonic() + cooldown_s,
                )

    def apply_runtime_surface_state(self, state: dict[str, Any]) -> None:
        """Apply one runtime surface-state snapshot onto the embodiment driver."""
        self.runtime_host_adapter.apply_runtime_surface_state(
            self.runtime_tool_context,
            state,
        )

    async def handle_runtime_browser_audio_chunk(
        self,
        event: BrowserAudioChunkEvent,
        outbound_queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """Feed one browser microphone audio chunk into the runtime speech session."""

        thread_id = str(event.thread_id or "app:main")
        session = self._runtime_browser_speech_sessions.get(
            self._build_runtime_browser_speech_session_key(
                connection_key=id(outbound_queue),
                thread_id=thread_id,
                session_id=str(event.session_id or thread_id),
                user_id=str(event.user_id or "user"),
            )
        )
        if session is None:
            session = self._build_runtime_browser_speech_session(event, outbound_queue)
            if session is None:
                await outbound_queue.put(
                    self._build_turn_error_event(
                        thread_id=thread_id,
                        error="Speech input is not configured for this runtime.",
                    )
                )
                return

        audio_chunk = decode_browser_audio_chunk(event.audio_b64)
        if audio_chunk is None or audio_chunk.size == 0:
            await outbound_queue.put(
                self._build_turn_error_event(
                    thread_id=thread_id,
                    error="Browser audio payload was empty or malformed.",
                )
            )
            return

        try:
            await session.feed_audio_frame(audio_chunk, int(event.sample_rate_hz))
        except Exception as exc:
            await outbound_queue.put(
                self._build_turn_error_event(
                    thread_id=thread_id,
                    error=str(exc),
                )
            )

    async def handle_runtime_browser_audio_stop(
        self,
        event: BrowserAudioStopEvent,
        outbound_queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """Flush the browser microphone session after capture stops."""

        thread_id = str(event.thread_id or "app:main")
        session = self._runtime_browser_speech_sessions.get(
            self._build_runtime_browser_speech_session_key(
                connection_key=id(outbound_queue),
                thread_id=thread_id,
                session_id=str(event.session_id or thread_id),
                user_id=str(event.user_id or "user"),
            )
        )
        if session is None:
            return

        try:
            await session.finish_capture()
        except Exception as exc:
            await outbound_queue.put(
                self._build_turn_error_event(
                    thread_id=thread_id,
                    error=str(exc),
                )
            )

    async def _cleanup_runtime_browser_speech_sessions(
        self,
        *,
        connection_key: int,
        flush: bool,
    ) -> None:
        """Release browser speech sessions owned by one websocket connection."""

        owned_keys = [
            key
            for key in self._runtime_browser_speech_sessions
            if key.startswith(f"{connection_key}:")
        ]
        for key in owned_keys:
            session = self._runtime_browser_speech_sessions.pop(key, None)
            if session is None:
                continue
            try:
                await session.close(flush=flush)
            except Exception as exc:
                self.logger.warning("Failed to close browser speech session %s: %s", key, exc)

    @staticmethod
    def _build_runtime_browser_speech_session_key(
        *,
        connection_key: int,
        thread_id: str,
        session_id: str,
        user_id: str,
    ) -> str:
        return f"{connection_key}:{thread_id}:{session_id}:{user_id}"

    def _build_runtime_browser_speech_session(
        self,
        event: BrowserAudioChunkEvent,
        outbound_queue: asyncio.Queue[dict[str, Any]],
    ) -> Any | None:
        """Build or reuse the browser microphone session for one websocket client."""

        config = getattr(self.runtime_config, "speech_input", None)
        if config is None or not getattr(config, "enabled", False):
            return None

        from reachy_mini.runtime.speech_session import RuntimeSpeechSession

        thread_id = str(event.thread_id or "app:main")
        session_id = str(event.session_id or thread_id)
        user_id = str(event.user_id or "user")
        speech_provider = self._create_runtime_speech_input_provider()
        if speech_provider is None:
            return None

        async def on_speech_started(_: str) -> None:
            await dispatch_user_speech_event(
                self,
                UserSpeechStartedEvent(
                    thread_id=thread_id,
                    session_id=session_id,
                    user_id=user_id,
                    text="",
                ),
                outbound_queue,
            )

        async def on_speech_stopped(_: str) -> None:
            await dispatch_user_speech_event(
                self,
                UserSpeechStoppedEvent(
                    thread_id=thread_id,
                    session_id=session_id,
                    user_id=user_id,
                    text="",
                ),
                outbound_queue,
            )

        async def on_user_text_partial(transcript: str) -> None:
            resolved_transcript = str(transcript or "").strip()
            if not resolved_transcript:
                return
            await dispatch_user_speech_event(
                self,
                UserSpeechPartialEvent(
                    thread_id=thread_id,
                    session_id=session_id,
                    user_id=user_id,
                    text=resolved_transcript,
                ),
                outbound_queue,
            )

        async def on_user_text(transcript: str) -> None:
            resolved_transcript = str(transcript or "").strip()
            if not resolved_transcript:
                return
            await dispatch_user_turn(
                self,
                UserTextEvent(
                    thread_id=thread_id,
                    session_id=session_id,
                    user_id=user_id,
                    text=resolved_transcript,
                ),
                outbound_queue,
            )

        session_key = self._build_runtime_browser_speech_session_key(
            connection_key=id(outbound_queue),
            thread_id=thread_id,
            session_id=session_id,
            user_id=user_id,
        )
        session = RuntimeSpeechSession(
            provider=speech_provider,
            config=config,
            logger=self.logger,
            on_speech_started=on_speech_started,
            on_speech_stopped=on_speech_stopped,
            on_user_text=on_user_text,
            on_user_text_partial=on_user_text_partial,
            input_blocked=self._runtime_speech_input_is_blocked,
            source_name="browser-microphone",
        )
        self._runtime_browser_speech_sessions[session_key] = session
        return session

    def stop(self) -> None:
        """Stop the app gracefully."""
        self.stop_event.set()
        print("App is stopping...")

    def _get_instance_path(self) -> Path:
        """Get the file path of the app instance."""
        module_name = type(self).__module__
        mod = importlib.import_module(module_name)
        assert mod.__file__ is not None

        return Path(mod.__file__).resolve()

    def resolve_profile_root(self) -> Path | None:
        """Resolve the configured profile root for resident-runtime apps."""
        configured = self.profile_root_relative_path
        if configured is None:
            return None

        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()

        app_root = self._get_instance_path().parent.parent
        return (app_root / candidate).resolve()

    def build_runtime(self, profile_root: Path) -> "RuntimeScheduler":
        """Build the resident runtime for the configured profile root."""
        from reachy_mini.runtime.config import load_profile_runtime_config
        from reachy_mini.runtime.profile_loader import load_profile_bundle
        from reachy_mini.runtime.scheduler import RuntimeScheduler

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        self.runtime_config = config
        return RuntimeScheduler.from_profile(
            profile=profile,
            config=config,
            runtime_tool_context=self.runtime_tool_context,
        )

    def wait_until_runtime_ready(self, timeout: float = 10.0) -> bool:
        """Block until the resident runtime is ready."""
        return self.runtime_ready.wait(timeout)

    def chat(self, payload: ChatRequest) -> ChatResponse:
        """Send one message to the resident runtime and wait for the final reply."""
        message = str(payload.message or "").strip()
        if not message:
            raise RuntimeError("Message cannot be empty.")

        loop = self.runtime_loop
        runtime = self.runtime
        if loop is None or runtime is None or not self.runtime_ready.is_set():
            raise RuntimeError("Runtime is not ready yet.")

        future = asyncio.run_coroutine_threadsafe(
            self._chat_turn(
                ChatRequest(
                    message=message,
                    thread_id=payload.thread_id,
                    session_id=payload.session_id,
                    user_id=payload.user_id,
                )
            ),
            loop,
        )
        try:
            return future.result(timeout=self.runtime_request_timeout)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError("Timed out waiting for the runtime reply.") from exc

    def run_resident_runtime(self, stop_event: threading.Event) -> None:
        """Keep the resident runtime alive until the app stops."""
        if self.profile_root is None:
            raise RuntimeError("profile_root_relative_path is not configured.")

        loop = asyncio.new_event_loop()
        self.runtime_loop = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_resident_runtime(stop_event))
        finally:
            self.runtime_ready.clear()
            self.runtime_loop = None
            asyncio.set_event_loop(None)
            loop.close()

    def _mount_runtime_socket(self) -> None:
        """Attach the runtime WebSocket onto the settings app."""
        assert self.settings_app is not None
        @self.settings_app.websocket("/ws/agent")
        async def runtime_socket(websocket: WebSocket) -> None:
            await self.handle_runtime_websocket(websocket)

    async def handle_runtime_websocket(self, websocket: WebSocket) -> None:
        """Handle one browser WebSocket attached to the resident runtime."""
        await websocket.accept()

        outbound_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        tracked_thread_ids: set[str] = {"app:main"}
        await outbound_queue.put(self._build_runtime_status_event())
        if self.runtime_ready.is_set():
            await self._publish_runtime_snapshot(
                outbound_queue,
                thread_id="app:main",
            )
        ready_task: asyncio.Task[None] | None = None
        if not self.runtime_ready.is_set():
            ready_task = asyncio.create_task(
                self._wait_and_publish_runtime_ready(
                    outbound_queue,
                    thread_id="app:main",
                )
            )

        send_task = asyncio.create_task(
            self._runtime_websocket_send_loop(websocket, outbound_queue)
        )
        front_output_task = asyncio.create_task(
            self._runtime_websocket_front_output_loop(
                outbound_queue=outbound_queue,
                tracked_thread_ids=tracked_thread_ids,
            )
        )
        try:
            await self._runtime_websocket_recv_loop(
                websocket,
                outbound_queue,
                tracked_thread_ids=tracked_thread_ids,
            )
        except WebSocketDisconnect:
            return
        finally:
            if ready_task is not None:
                ready_task.cancel()
                try:
                    await ready_task
                except asyncio.CancelledError:
                    pass
            await self._cleanup_runtime_browser_speech_sessions(
                connection_key=id(outbound_queue),
                flush=False,
            )
            front_output_task.cancel()
            try:
                await front_output_task
            except asyncio.CancelledError:
                pass
            send_task.cancel()
            try:
                await send_task
            except (asyncio.CancelledError, RuntimeError, WebSocketDisconnect):
                pass

    async def _run_resident_runtime(self, stop_event: threading.Event) -> None:
        assert self.profile_root is not None

        runtime = self.build_runtime(self.profile_root)
        self.runtime = runtime
        microphone_bridge_task: asyncio.Task[None] | None = None
        await runtime.start()
        self.runtime_ready.set()
        microphone_bridge = self._build_runtime_microphone_bridge(runtime)
        self.runtime_microphone_bridge = microphone_bridge
        if microphone_bridge is not None:
            microphone_bridge_task = asyncio.create_task(
                microphone_bridge.run(),
                name="resident-runtime-microphone",
            )
        try:
            while not stop_event.is_set():
                await asyncio.sleep(0.25)
        finally:
            if microphone_bridge is not None:
                try:
                    await microphone_bridge.stop()
                except Exception as exc:
                    self.logger.warning("Failed to stop runtime microphone bridge: %s", exc)
            if microphone_bridge_task is not None:
                try:
                    await microphone_bridge_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    self.logger.warning("Runtime microphone bridge failed: %s", exc)
            self.runtime_microphone_bridge = None
            self.runtime_ready.clear()
            try:
                await runtime.stop()
            finally:
                self.runtime = None

    def _create_runtime_speech_input_provider(self) -> Any | None:
        """Build one streaming speech-input provider for the current runtime config."""

        config = getattr(self.runtime_config, "speech_input", None)
        if config is None or not getattr(config, "enabled", False):
            return None

        try:
            from reachy_mini.runtime.speech_session import (
                build_runtime_speech_session_provider,
            )

            return build_runtime_speech_session_provider(config=config)
        except Exception as exc:
            self.logger.warning("Failed to build speech input provider: %s", exc)
            return None

    def _runtime_speech_input_is_blocked(self) -> bool:
        """Whether user speech capture should stay blocked during runtime playback."""

        if time.monotonic() < self._runtime_speech_input_block_until:
            return True
        context = self.runtime_tool_context
        speech_driver = getattr(context, "speech_driver", None)
        if speech_driver is not None and getattr(speech_driver, "speech_active", False):
            return True
        reply_audio_service = getattr(context, "reply_audio_service", None)
        active_playback_task = getattr(reply_audio_service, "_active_playback_task", None)
        return active_playback_task is not None and not active_playback_task.done()

    def _build_runtime_microphone_bridge(self, runtime: "RuntimeScheduler") -> Any | None:
        """Build the optional robot-microphone bridge for the resident runtime."""

        config = getattr(self.runtime_config, "speech_input", None)
        if config is None or not getattr(config, "enabled", False):
            return None

        context = self.runtime_tool_context
        reachy_mini = getattr(context, "reachy_mini", None)
        media = getattr(reachy_mini, "media", None)
        if media is None:
            self.logger.warning("Runtime microphone bridge skipped: media is unavailable.")
            return None

        try:
            from reachy_mini.runtime.speech_session import RuntimeMicrophoneBridge
        except Exception as exc:
            self.logger.warning("Failed to load speech session runtime bridge: %s", exc)
            return None

        speech_provider = self._create_runtime_speech_input_provider()
        if speech_provider is None:
            return None

        thread_id = "app:main"
        session_id = thread_id
        user_id = "user"

        async def surface_state_handler(state: dict[str, Any]) -> None:
            self.apply_runtime_surface_state(state)

        async def on_speech_started(_: str) -> None:
            await runtime.handle_user_speech_started(
                thread_id=thread_id,
                session_id=session_id,
                user_id=user_id,
                user_text="",
                surface_state_handler=surface_state_handler,
            )

        async def on_speech_stopped(_: str) -> None:
            await runtime.handle_user_speech_stopped(
                thread_id=thread_id,
                session_id=session_id,
                user_id=user_id,
                user_text="",
                surface_state_handler=surface_state_handler,
            )

        async def on_user_text_partial(transcript: str) -> None:
            resolved_transcript = str(transcript or "").strip()
            if not resolved_transcript:
                return
            await runtime.handle_user_speech_partial(
                thread_id=thread_id,
                session_id=session_id,
                user_id=user_id,
                user_text=resolved_transcript,
                surface_state_handler=surface_state_handler,
            )

        async def on_user_text(transcript: str) -> None:
            resolved_transcript = str(transcript or "").strip()
            if not resolved_transcript:
                return
            await runtime.handle_user_turn(
                thread_id=thread_id,
                session_id=session_id,
                user_id=user_id,
                user_text=resolved_transcript,
                surface_state_handler=surface_state_handler,
                final_reply_handler=self.play_runtime_reply_audio,
            )

        return RuntimeMicrophoneBridge(
            media=media,
            provider=speech_provider,
            config=config,
            logger=self.logger,
            on_speech_started=on_speech_started,
            on_speech_stopped=on_speech_stopped,
            on_user_text=on_user_text,
            on_user_text_partial=on_user_text_partial,
            input_blocked=self._runtime_speech_input_is_blocked,
        )

    def _get_runtime_speech_input_playback_cooldown_s(self) -> float:
        config = getattr(self.runtime_config, "speech_input", None)
        cooldown_ms = getattr(config, "playback_block_cooldown_ms", 0)
        return max(float(cooldown_ms), 0.0) / 1000.0

    async def _chat_turn(self, payload: ChatRequest) -> ChatResponse:
        runtime = self.runtime
        if runtime is None:
            raise RuntimeError("Runtime is not ready yet.")

        thread_id = str(payload.thread_id or "app:main")
        session_id = str(payload.session_id or thread_id)
        user_id = str(payload.user_id or "user")
        queue = runtime.subscribe_front_outputs()
        final_reply = ""
        final_error = ""
        front_decision: dict[str, Any] | None = None
        front_tool_results: list[dict[str, Any]] = []

        async def surface_state_handler(state: dict[str, Any]) -> None:
            self.apply_runtime_surface_state(state)

        try:
            await runtime.handle_user_turn(
                thread_id=thread_id,
                session_id=session_id,
                user_id=user_id,
                user_text=str(payload.message),
                surface_state_handler=surface_state_handler,
                final_reply_handler=self.play_runtime_reply_audio,
            )
            await runtime.wait_for_thread_idle(thread_id)
            await asyncio.sleep(0)

            while not queue.empty():
                packet = queue.get_nowait()
                try:
                    if packet.thread_id != thread_id:
                        continue
                    if packet.type == "front_final_done":
                        final_reply = str(packet.text or "").strip()
                    elif packet.type == "front_decision" and packet.payload is not None:
                        packet_decision = dict(packet.payload)
                        if (
                            front_decision is None
                            or str(packet_decision.get("signal_name", "") or "") == "user_turn"
                            or str(front_decision.get("signal_name", "") or "") != "user_turn"
                        ):
                            front_decision = packet_decision
                    elif packet.type == "front_tool_result" and packet.payload is not None:
                        front_tool_results.append(dict(packet.payload))
                    elif packet.type == "turn_error":
                        final_error = str(packet.error or "").strip()
                finally:
                    queue.task_done()
        finally:
            runtime.unsubscribe_front_outputs(queue)

        return ChatResponse(
            thread_id=thread_id,
            reply=final_reply,
            error=final_error,
            surface_state=runtime.get_thread_surface_state(thread_id),
            front_decision=front_decision or runtime.get_thread_front_decision(thread_id),
            front_tool_results=front_tool_results,
        )

    async def _wait_and_publish_runtime_ready(
        self,
        outbound_queue: asyncio.Queue[dict[str, Any]],
        *,
        thread_id: str = "app:main",
    ) -> None:
        """Publish a ready status event once the runtime becomes ready."""
        while not self.runtime_ready.is_set():
            await asyncio.to_thread(self.runtime_ready.wait, 0.25)
            if self.runtime_ready.is_set():
                break
        await outbound_queue.put(self._build_runtime_status_event())
        await self._publish_runtime_snapshot(
            outbound_queue,
            thread_id=thread_id,
        )

    async def _publish_runtime_snapshot(
        self,
        outbound_queue: asyncio.Queue[dict[str, Any]],
        *,
        thread_id: str,
    ) -> None:
        """Publish the current runtime-visible state snapshot for one thread."""
        runtime = self.runtime
        if runtime is None or not self.runtime_ready.is_set():
            return

        surface_state = runtime.get_thread_surface_state(thread_id)
        if surface_state is not None:
            await outbound_queue.put(
                {
                    "type": "surface_state",
                    "thread_id": thread_id,
                    "state": dict(surface_state),
                }
            )

        front_decision = runtime.get_thread_front_decision(thread_id)
        if front_decision is not None:
            await outbound_queue.put(
                {
                    "type": "front_decision",
                    "thread_id": thread_id,
                    "turn_id": str(front_decision.get("turn_id", "") or ""),
                    "payload": dict(front_decision),
                }
            )

    async def _runtime_websocket_send_loop(
        self,
        websocket: WebSocket,
        outbound_queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """Send queued runtime events to the browser."""
        while True:
            event = await outbound_queue.get()
            try:
                await websocket.send_json(event)
            finally:
                outbound_queue.task_done()

    async def _runtime_websocket_recv_loop(
        self,
        websocket: WebSocket,
        outbound_queue: asyncio.Queue[dict[str, Any]],
        *,
        tracked_thread_ids: set[str],
    ) -> None:
        """Receive browser messages and dispatch resident-runtime turns."""
        while True:
            payload = await websocket.receive_json()
            await handle_runtime_websocket_payload(
                self,
                payload=payload,
                outbound_queue=outbound_queue,
                tracked_thread_ids=tracked_thread_ids,
            )

    async def _runtime_websocket_front_output_loop(
        self,
        *,
        outbound_queue: asyncio.Queue[dict[str, Any]],
        tracked_thread_ids: set[str],
    ) -> None:
        """Forward runtime front-output packets for this websocket."""
        runtime_queue: asyncio.Queue[Any] | None = None
        runtime = None

        try:
            while runtime_queue is None:
                runtime = self.runtime
                if runtime is not None and self.runtime_ready.is_set():
                    runtime_queue = runtime.subscribe_front_outputs()
                    break
                await asyncio.sleep(0.1)

            while True:
                packet = await runtime_queue.get()
                try:
                    packet_thread_id = str(getattr(packet, "thread_id", "") or "")
                    if (
                        not tracked_thread_ids
                        or packet_thread_id not in tracked_thread_ids
                    ):
                        continue
                    await outbound_queue.put(packet.as_event())
                finally:
                    runtime_queue.task_done()
        finally:
            if runtime is not None and runtime_queue is not None:
                runtime.unsubscribe_front_outputs(runtime_queue)

    def _build_runtime_status_event(self) -> dict[str, Any]:
        """Build one small runtime readiness event."""
        speech_input_config = getattr(self.runtime_config, "speech_input", None)
        speech_input_enabled = False
        if speech_input_config is not None and getattr(speech_input_config, "enabled", False):
            speech_input_enabled = self._create_runtime_speech_input_provider() is not None
        return {
            "type": "runtime_status",
            "ready": self.runtime_ready.is_set(),
            "profile_root": str(self.profile_root) if self.profile_root is not None else "",
            "speech_input_enabled": speech_input_enabled,
        }

    @staticmethod
    def _build_turn_error_event(*, thread_id: str, error: str) -> dict[str, Any]:
        """Build one turn-level error event."""
        return {
            "type": "turn_error",
            "thread_id": thread_id,
            "error": str(error or "").strip(),
        }
