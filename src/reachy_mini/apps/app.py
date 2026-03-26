"""Reachy Mini application base classes and helpers."""

import asyncio
import argparse
import importlib
import logging
import threading
import traceback
from collections.abc import Awaitable, Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from reachy_mini.reachy_mini import ReachyMini

if TYPE_CHECKING:
    from reachy_mini.runtime.scheduler import RuntimeScheduler


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


class RuntimeStatusResponse(BaseModel):
    """Small readiness payload for the resident runtime."""

    ready: bool
    profile_root: str


class UserTextEvent(BaseModel):
    """One browser-to-app user text event over WebSocket."""

    type: Literal["user_text"]
    text: str = Field(min_length=1)
    thread_id: str = "app:main"
    session_id: str | None = None
    user_id: str = "user"


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
        self.runtime: RuntimeScheduler | None = None
        self.runtime_loop: asyncio.AbstractEventLoop | None = None
        self.runtime_ready = threading.Event()

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

        _ = reachy_mini
        self.run_resident_runtime(stop_event)

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
        return RuntimeScheduler.from_profile(profile=profile, config=config)

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
        await outbound_queue.put(self._build_runtime_status_event())
        ready_task: asyncio.Task[None] | None = None
        if not self.runtime_ready.is_set():
            ready_task = asyncio.create_task(
                self._wait_and_publish_runtime_ready(outbound_queue)
            )

        send_task = asyncio.create_task(
            self._runtime_websocket_send_loop(websocket, outbound_queue)
        )
        try:
            await self._runtime_websocket_recv_loop(websocket, outbound_queue)
        except WebSocketDisconnect:
            return
        finally:
            if ready_task is not None:
                ready_task.cancel()
                try:
                    await ready_task
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
        await runtime.start()
        self.runtime_ready.set()
        try:
            while not stop_event.is_set():
                await asyncio.sleep(0.25)
        finally:
            self.runtime_ready.clear()
            try:
                await runtime.stop()
            finally:
                self.runtime = None

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

        try:
            await runtime.handle_user_text(
                thread_id=thread_id,
                session_id=session_id,
                user_id=user_id,
                user_text=str(payload.message),
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
        )

    async def _wait_and_publish_runtime_ready(
        self,
        outbound_queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """Publish a ready status event once the runtime becomes ready."""
        while not self.runtime_ready.is_set():
            await asyncio.to_thread(self.runtime_ready.wait, 0.25)
            if self.runtime_ready.is_set():
                break
        await outbound_queue.put(self._build_runtime_status_event())

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
    ) -> None:
        """Receive browser messages and execute resident-runtime turns."""
        while True:
            payload = await websocket.receive_json()
            event_type = str(payload.get("type", "") or "").strip()

            if event_type == "ping":
                await outbound_queue.put({"type": "pong"})
                continue

            if event_type != "user_text":
                await outbound_queue.put(
                    self._build_turn_error_event(
                        thread_id=str(payload.get("thread_id", "app:main") or "app:main"),
                        error=f"Unsupported WebSocket event type: {event_type or '<empty>'}",
                    )
                )
                continue

            try:
                event = UserTextEvent.model_validate(payload)
            except ValidationError as exc:
                await outbound_queue.put(
                    self._build_turn_error_event(
                        thread_id=str(payload.get("thread_id", "app:main") or "app:main"),
                        error=str(exc),
                    )
                )
                continue

            await self._stream_user_text_turn(event, outbound_queue)

    async def _stream_user_text_turn(
        self,
        event: UserTextEvent,
        outbound_queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """Execute one resident-runtime turn and forward its events live."""
        runtime = self.runtime
        thread_id = str(event.thread_id or "app:main")
        runtime_loop = self.runtime_loop
        if runtime is None or runtime_loop is None or not self.runtime_ready.is_set():
            await outbound_queue.put(
                self._build_turn_error_event(
                    thread_id=thread_id,
                    error="Runtime is not ready yet.",
                )
            )
            return

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._stream_user_text_turn_on_runtime_loop(
                    thread_id=thread_id,
                    session_id=str(event.session_id or thread_id),
                    user_id=str(event.user_id or "user"),
                    user_text=str(event.text),
                    outbound_queue=outbound_queue,
                    outbound_loop=asyncio.get_running_loop(),
                ),
                runtime_loop,
            )
            await asyncio.wrap_future(future)
        except Exception as exc:
            await outbound_queue.put(
                self._build_turn_error_event(
                    thread_id=thread_id,
                    error=str(exc),
                )
            )

    async def _stream_user_text_turn_on_runtime_loop(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str,
        outbound_queue: asyncio.Queue[dict[str, Any]],
        outbound_loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Run one turn on the resident runtime loop and mirror events to the WS loop."""
        runtime = self.runtime
        if runtime is None:
            raise RuntimeError("Runtime is not ready yet.")

        runtime_queue = runtime.subscribe_front_outputs()
        turn_finished = asyncio.Event()

        async def publish_event(event: dict[str, Any]) -> None:
            put_future = asyncio.run_coroutine_threadsafe(
                outbound_queue.put(event),
                outbound_loop,
            )
            await asyncio.wrap_future(put_future)

        forward_task = asyncio.create_task(
            self._forward_runtime_packets(
                runtime_queue=runtime_queue,
                publish_event=publish_event,
                thread_id=thread_id,
                turn_finished=turn_finished,
            )
        )

        async def surface_state_handler(state: dict[str, Any]) -> None:
            await publish_event(
                {
                    "type": "surface_state",
                    "thread_id": thread_id,
                    "state": dict(state),
                }
            )

        try:
            await runtime.handle_user_text(
                thread_id=thread_id,
                session_id=session_id,
                user_id=user_id,
                user_text=user_text,
                surface_state_handler=surface_state_handler,
            )
            await runtime.wait_for_thread_idle(thread_id)
        except Exception:
            raise
        finally:
            turn_finished.set()
            try:
                await forward_task
            finally:
                runtime.unsubscribe_front_outputs(runtime_queue)

    async def _forward_runtime_packets(
        self,
        *,
        runtime_queue: asyncio.Queue[Any],
        publish_event: Callable[[dict[str, Any]], Awaitable[None]],
        thread_id: str,
        turn_finished: asyncio.Event,
    ) -> None:
        """Forward runtime packets to the browser as WebSocket events."""
        while True:
            if turn_finished.is_set() and runtime_queue.empty():
                return

            try:
                packet = await asyncio.wait_for(runtime_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            try:
                if getattr(packet, "thread_id", "") != thread_id:
                    continue
                await publish_event(packet.as_event())
            finally:
                runtime_queue.task_done()

    def _build_runtime_status_event(self) -> dict[str, Any]:
        """Build one small runtime readiness event."""
        return {
            "type": "runtime_status",
            "ready": self.runtime_ready.is_set(),
            "profile_root": str(self.profile_root) if self.profile_root is not None else "",
        }

    @staticmethod
    def _build_turn_error_event(*, thread_id: str, error: str) -> dict[str, Any]:
        """Build one turn-level error event."""
        return {
            "type": "turn_error",
            "thread_id": thread_id,
            "error": str(error or "").strip(),
        }


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="App creation assistant for Reachy Mini."
    )
    subparsers = parser.add_subparsers(
        dest="command", help="Available commands", required=True
    )

    create_parser = subparsers.add_parser("create", help="Create a new app project")
    create_parser.add_argument(
        "app_name",
        type=str,
        nargs="?",
        default=None,
        help="Name of the app to create.",
    )
    create_parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=None,
        help="Path where the app project will be created.",
    )

    return parser.parse_args()


def main() -> None:
    """Entry point for the app assistant."""
    from rich.console import Console

    from . import assistant

    args = parse_args()
    if args.command == "create":
        assistant.create(Console(), app_name=args.app_name, app_path=args.path)


if __name__ == "__main__":
    main()
