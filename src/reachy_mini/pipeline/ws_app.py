"""FastAPI websocket app bound to a v4 RuntimeSession."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .frames import (
    AudioFrame,
    BrowserInputFrame,
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
    await websocket.accept()
    sub = session.subscribe()
    pong_event = asyncio.Event()
    pong_event.set()
    send_lock = asyncio.Lock()
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
        await _recv_loop(session, websocket, pong_event, send_lock)
    except WebSocketDisconnect:
        return
    finally:
        for task in (send_task, heartbeat_task):
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, BaseException):
            await send_task
        with contextlib.suppress(asyncio.CancelledError, BaseException):
            await heartbeat_task
        session.unsubscribe(sub)


async def _recv_loop(
    session: RuntimeSession,
    websocket: WebSocket,
    pong_event: asyncio.Event,
    send_lock: asyncio.Lock,
) -> None:
    while True:
        try:
            message = await websocket.receive_json()
        except WebSocketDisconnect:
            raise
        try:
            frame = decode_inbound(message)
        except WireDecodeError as exc:
            reason = "legacy_protocol_rejected" if "Legacy protocol" in str(exc) else str(exc)
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
        await _route_inbound(session, frame)


async def _route_inbound(session: RuntimeSession, frame: Any) -> None:
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
            LOGGER.warning("Skipping non-serializable frame %s: %s", type(frame).__name__, exc)
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
