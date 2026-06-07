"""FastAPI websocket app bound to a v4 RuntimeSession."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .frames import (
    AudioFrame,
    BrowserInputFrame,
    CameraFrame,
    PipelineErrorFrame,
    SpeechActivityFrame,
    TranscriptionFrame,
    VisionEventFrame,
)
from .session import RuntimeSession
from .wire import (
    WireDecodeError,
    WireSerializationError,
    _AudioStop,
    _Ping,
    _Pong,
    decode_inbound,
    encode_frame,
)

LOGGER = logging.getLogger(__name__)

WS_PATH = "/ws/agent"
HEARTBEAT_INTERVAL_S = 15.0
HEARTBEAT_TIMEOUT_S = 5.0
InboundFrameHandler = Callable[[Any], bool | Awaitable[bool]]


def build_ws_app(session: RuntimeSession, *, path: str = WS_PATH) -> FastAPI:
    """Build a FastAPI app exposing one websocket bound to a RuntimeSession."""
    app = FastAPI()

    @app.websocket(path)
    async def runtime_socket(websocket: WebSocket) -> None:
        await _serve_websocket(session, websocket)

    return app


async def run_ws_app(
    app: FastAPI,
    *,
    host: str,
    port: int,
    startup_timeout: float,
) -> None:
    """Run the v4 websocket app with uvicorn."""
    config = uvicorn.Config(app, host=host, port=port, lifespan="on")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        await _wait_for_uvicorn_startup(server, serve_task, startup_timeout)
        await serve_task
    finally:
        server.should_exit = True
        if not serve_task.done():
            serve_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serve_task


async def _serve_websocket(session: RuntimeSession, websocket: WebSocket) -> None:
    """Accept one websocket connection and pump frames in both directions."""
    await _serve_websocket_with_handler(session, websocket, inbound_handler=None)


async def _serve_websocket_with_handler(
    session: RuntimeSession,
    websocket: WebSocket,
    *,
    inbound_handler: InboundFrameHandler | None = None,
) -> None:
    """Accept one websocket connection and pump frames with an optional hook."""
    await websocket.accept()
    sub = session.subscribe()
    pong_event = asyncio.Event()
    pong_event.set()
    send_lock = asyncio.Lock()
    route_lock = asyncio.Lock()
    route_tasks: set[asyncio.Task[None]] = set()
    send_task = asyncio.create_task(_send_loop(websocket, sub, send_lock))
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            websocket,
            pong_event,
            send_lock,
            interval_s=HEARTBEAT_INTERVAL_S,
            timeout_s=HEARTBEAT_TIMEOUT_S,
        )
    )
    try:
        await _recv_loop(
            session,
            websocket,
            pong_event,
            send_lock,
            route_lock,
            route_tasks,
            inbound_handler,
        )
    except WebSocketDisconnect:
        return
    finally:
        for task in route_tasks:
            task.cancel()
        for task in (send_task, heartbeat_task, *route_tasks):
            task.cancel()
        for task in (send_task, heartbeat_task, *route_tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        session.unsubscribe(sub)


async def _recv_loop(
    session: RuntimeSession,
    websocket: WebSocket,
    pong_event: asyncio.Event,
    send_lock: asyncio.Lock,
    route_lock: asyncio.Lock,
    route_tasks: set[asyncio.Task[None]],
    inbound_handler: InboundFrameHandler | None,
) -> None:
    while True:
        try:
            message = await websocket.receive_json()
        except WebSocketDisconnect:
            raise
        try:
            frame = decode_inbound(message)
        except WireDecodeError as exc:
            reason = (
                "legacy_protocol_rejected"
                if "Legacy protocol" in str(exc)
                else str(exc)
            )
            await _emit_error(websocket, "wire", reason)
            await websocket.close(code=1003)
            return

        if isinstance(frame, _Ping):
            async with send_lock:
                await websocket.send_json({"type": "pong", "payload": {}})
            continue
        if isinstance(frame, _Pong):
            pong_event.set()
            continue
        if isinstance(frame, _AudioStop):
            # Audio capture is stream-driven; reaching here just means the
            # browser stopped pushing chunks. Nothing to do server-side.
            continue
        if isinstance(frame, CameraFrame):
            _schedule_route(
                session,
                frame,
                route_lock=None,
                route_tasks=route_tasks,
                inbound_handler=inbound_handler,
            )
            continue
        _schedule_route(
            session,
            frame,
            route_lock=route_lock,
            route_tasks=route_tasks,
            inbound_handler=inbound_handler,
        )


def _schedule_route(
    session: RuntimeSession,
    frame: Any,
    route_lock: asyncio.Lock | None,
    route_tasks: set[asyncio.Task[None]],
    inbound_handler: InboundFrameHandler | None,
) -> None:
    task = asyncio.create_task(
        _route_inbound_serialized(session, frame, route_lock, inbound_handler)
    )
    route_tasks.add(task)
    task.add_done_callback(lambda completed: _finish_route_task(completed, route_tasks))


def _finish_route_task(
    task: asyncio.Task[None],
    route_tasks: set[asyncio.Task[None]],
) -> None:
    route_tasks.discard(task)
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        LOGGER.error(
            "Inbound route task failed",
            exc_info=(type(exc), exc, exc.__traceback__),
        )


async def _route_inbound_serialized(
    session: RuntimeSession,
    frame: Any,
    route_lock: asyncio.Lock | None,
    inbound_handler: InboundFrameHandler | None,
) -> None:
    if route_lock is None:
        await _route_inbound(session, frame, inbound_handler)
        return
    async with route_lock:
        await _route_inbound(session, frame, inbound_handler)


async def _route_inbound(
    session: RuntimeSession,
    frame: Any,
    inbound_handler: InboundFrameHandler | None = None,
) -> None:
    if inbound_handler is not None:
        handled = inbound_handler(frame)
        if asyncio.iscoroutine(handled):
            handled = await handled
        if handled:
            return
    if isinstance(frame, BrowserInputFrame):
        await session.submit_browser_input(frame)
        return
    if isinstance(frame, AudioFrame):
        await session.submit_audio_chunk(frame)
        return
    if isinstance(frame, SpeechActivityFrame):
        await session.submit_speech_activity(frame)
        return
    if isinstance(frame, TranscriptionFrame):
        await session.submit_transcription(frame)
        return
    if isinstance(frame, VisionEventFrame):
        await session.submit_vision_event(frame)
        return
    if isinstance(frame, CameraFrame):
        return
    LOGGER.warning("Unhandled inbound frame: %s", type(frame).__name__)


async def _send_loop(
    websocket: WebSocket,
    sub: Any,
    send_lock: asyncio.Lock,
) -> None:
    while True:
        frame = await sub.queue.get()
        try:
            envelope = encode_frame(frame)
        except WireSerializationError as exc:
            LOGGER.warning(
                "Skipping non-serializable frame %s: %s", type(frame).__name__, exc
            )
            continue
        try:
            async with send_lock:
                await websocket.send_json(envelope)
        except WebSocketDisconnect:
            return


async def _heartbeat_loop(
    websocket: WebSocket,
    pong_event: asyncio.Event,
    send_lock: asyncio.Lock,
    *,
    interval_s: float = HEARTBEAT_INTERVAL_S,
    timeout_s: float = HEARTBEAT_TIMEOUT_S,
) -> None:
    while True:
        await asyncio.sleep(interval_s)
        pong_event.clear()
        try:
            async with send_lock:
                await websocket.send_json({"type": "ping", "payload": {}})
            await asyncio.wait_for(pong_event.wait(), timeout=timeout_s)
        except WebSocketDisconnect:
            return
        except asyncio.TimeoutError:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await websocket.close(code=1001)
            return


async def _emit_error(websocket: WebSocket, component: str, reason: str) -> None:
    try:
        await websocket.send_json(
            encode_frame(PipelineErrorFrame(component=component, reason=reason))
        )
    except WebSocketDisconnect:
        return


async def _wait_for_uvicorn_startup(
    server: uvicorn.Server,
    serve_task: asyncio.Task[None],
    timeout: float,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not server.started:
        if serve_task.done():
            await serve_task
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Timed out waiting for websocket server startup.")
        await asyncio.sleep(0.01)
