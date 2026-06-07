"""Reachy Mini application base classes wired to the v4 RuntimeSession."""

from __future__ import annotations

import asyncio
import base64
import importlib
import logging
import threading
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from reachy_mini.pipeline.frames import CameraFrame, VisionEventFrame
from reachy_mini.pipeline.live_io import MicrophoneSource
from reachy_mini.pipeline.session import RuntimeSession
from reachy_mini.pipeline.ws_app import _serve_websocket_with_handler

from .runtime_host import AppRuntimeHostAdapter

if TYPE_CHECKING:
    from reachy_mini.reachy_mini import ReachyMini
    from reachy_mini.runtime.tools import ReachyToolContext


class RuntimeStatusResponse(BaseModel):
    """Small readiness payload for the resident runtime."""

    ready: bool
    profile_root: str
    speech_input_enabled: bool = False


SessionFactory = Callable[[Path, Any], RuntimeSession]


class ReachyMiniApp:
    """Base class for Reachy Mini applications.

    The app owns:
    - the FastAPI settings_app (mounted on ``custom_app_url``)
    - the resident v4 RuntimeSession bound to ``profile_root_relative_path``
    - an optional microphone bridge driven by the runtime config
    """

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

        self.daemon_on_localhost = self._check_daemon_on_localhost()
        self.logger.info(f"Daemon on localhost: {self.daemon_on_localhost}")

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
        self.runtime: RuntimeSession | None = None
        self.runtime_loop: asyncio.AbstractEventLoop | None = None
        self.runtime_ready = threading.Event()
        self.runtime_tool_context: Any | None = None
        self.runtime_microphone: MicrophoneSource | None = None
        self._runtime_camera_ingest_busy = threading.Event()
        self._runtime_camera_vision_listener: Any | None = None

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
        """Check if daemon is reachable on localhost."""
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
        """Run the main logic of the app."""
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
        return self.runtime_host_adapter.reset_runtime_audio_motion(
            self.runtime_tool_context
        )

    async def play_runtime_reply_audio(self, payload: dict[str, Any]) -> bool:
        """Synthesize and play one final runtime reply."""
        return await self.runtime_host_adapter.play_runtime_reply_audio(
            self.runtime_tool_context,
            payload,
        )

    def apply_runtime_surface_state(self, state: dict[str, Any]) -> None:
        """Apply one runtime surface-state snapshot onto the embodiment driver."""
        self.runtime_host_adapter.apply_runtime_surface_state(
            self.runtime_tool_context,
            state,
        )

    def stop(self) -> None:
        """Stop the app gracefully."""
        self.stop_event.set()
        print("App is stopping...")

    def build_runtime(self, profile_root: Path) -> RuntimeSession:
        """Build a v4 RuntimeSession for the configured profile root."""
        mini = getattr(self.runtime_tool_context, "reachy_mini", None)

        def _mini_factory(_config: Any) -> Any:
            return mini if mini is not None else _AppNoopMini()

        return RuntimeSession.from_profile(
            profile_root,
            mini_factory=_mini_factory,
        )

    def wait_until_runtime_ready(self, timeout: float = 10.0) -> bool:
        """Block until the resident runtime is ready."""
        return self.runtime_ready.wait(timeout)

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
        """Delegate the websocket lifecycle to pipeline.ws_app."""
        if not self.runtime_ready.is_set():
            await asyncio.to_thread(self.runtime_ready.wait, 5.0)
        runtime = self.runtime
        if runtime is None:
            try:
                await websocket.accept()
                await websocket.close(code=1011)
            except WebSocketDisconnect:
                pass
            return
        try:
            await _serve_websocket_with_handler(
                runtime,
                websocket,
                inbound_handler=self._handle_runtime_inbound_frame,
            )
        except WebSocketDisconnect:
            return

    async def _run_resident_runtime(self, stop_event: threading.Event) -> None:
        assert self.profile_root is not None

        runtime = self.build_runtime(self.profile_root)
        self.runtime = runtime
        await runtime.start()
        self._attach_reactive_vision_bridge(runtime)
        self.runtime_ready.set()
        microphone = await self._build_runtime_microphone(runtime)
        self.runtime_microphone = microphone
        try:
            while not stop_event.is_set():
                await asyncio.sleep(0.25)
        finally:
            self._detach_reactive_vision_bridge()
            if microphone is not None:
                try:
                    await microphone.stop()
                except Exception as exc:
                    self.logger.warning("Failed to stop runtime microphone: %s", exc)
            self.runtime_microphone = None
            self.runtime_ready.clear()
            try:
                await runtime.stop()
            finally:
                self.runtime = None

    async def _handle_runtime_inbound_frame(self, frame: Any) -> bool:
        if not isinstance(frame, CameraFrame):
            return False
        context = self.runtime_tool_context
        camera_worker = getattr(context, "camera_worker", None)
        if camera_worker is None or not hasattr(camera_worker, "ingest_external_frame"):
            return True
        if self._runtime_camera_ingest_busy.is_set():
            return True
        image = self._decode_browser_camera_frame(frame)
        if image is None:
            return True
        self._runtime_camera_ingest_busy.set()
        asyncio.create_task(self._ingest_runtime_camera_frame(camera_worker, image))
        return True

    async def _ingest_runtime_camera_frame(
        self,
        camera_worker: Any,
        image: Any,
    ) -> None:
        try:
            await asyncio.to_thread(camera_worker.ingest_external_frame, image)
        except Exception as exc:
            self.logger.warning("Failed to process browser camera frame: %s", exc)
        finally:
            self._runtime_camera_ingest_busy.clear()

    def _decode_browser_camera_frame(self, frame: CameraFrame) -> Any | None:
        try:
            import cv2
            import numpy as np

            raw = base64.b64decode(frame.image_b64, validate=True)
            encoded = np.frombuffer(raw, dtype=np.uint8)
            return cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        except Exception as exc:
            self.logger.warning("Failed to decode browser camera frame: %s", exc)
            return None

    def _attach_reactive_vision_bridge(self, runtime: RuntimeSession) -> None:
        context = self.runtime_tool_context
        camera_worker = getattr(context, "camera_worker", None)
        if camera_worker is None or not hasattr(
            camera_worker, "add_reactive_vision_listener"
        ):
            return
        self._detach_reactive_vision_bridge()

        loop = asyncio.get_running_loop()

        def on_event(event: Any) -> None:
            payload = dict(getattr(event, "metadata", {}) or {})
            ts_ms = int(getattr(event, "ts_monotonic", 0.0) * 1000)
            frame = VisionEventFrame(
                event=str(getattr(event, "name", "") or ""),
                payload=payload,
                ts_ms=ts_ms,
            )
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(runtime.submit_vision_event(frame))
            )

        camera_worker.add_reactive_vision_listener(on_event)
        self._runtime_camera_vision_listener = on_event

    def _detach_reactive_vision_bridge(self) -> None:
        listener = self._runtime_camera_vision_listener
        if listener is None:
            return
        self._runtime_camera_vision_listener = None
        context = self.runtime_tool_context
        camera_worker = getattr(context, "camera_worker", None)
        if camera_worker is None or not hasattr(
            camera_worker, "remove_reactive_vision_listener"
        ):
            return
        try:
            camera_worker.remove_reactive_vision_listener(listener)
        except Exception as exc:
            self.logger.warning("Failed to remove reactive vision listener: %s", exc)

    async def _build_runtime_microphone(
        self,
        runtime: RuntimeSession,
    ) -> MicrophoneSource | None:
        """Build the optional robot-microphone source bound to the v4 session."""
        config = getattr(runtime.config, "speech_input", None)
        if config is None or not getattr(config, "enabled", False):
            return None

        sample_rate = int(getattr(config, "sample_rate", 16000) or 16000)
        microphone = MicrophoneSource(sample_rate=sample_rate)

        async def on_frame(frame: Any) -> None:
            await runtime.submit_audio_chunk(frame)

        await microphone.start(on_frame)
        return microphone

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


class _AppNoopMini:
    """Fallback mini object used when the app has no hardware ReachyMini bound."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def goto_target(self, **kwargs: Any) -> None:
        self.calls.append({"method": "goto_target", "kwargs": kwargs})

    def look_at_world(self, x, y, z, duration, perform_movement) -> None:
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
