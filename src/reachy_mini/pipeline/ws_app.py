"""FastAPI websocket app bound to a v4 RuntimeSession."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

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
    decode_inbound,
    encode_frame,
)

LOGGER = logging.getLogger(__name__)

WS_PATH = "/ws/agent"


def build_ws_app(session: RuntimeSession, *, path: str = WS_PATH) -> FastAPI:
    """Build a FastAPI app exposing one websocket bound to a RuntimeSession."""
    app = FastAPI()

    @app.websocket(path)
    async def runtime_socket(websocket: WebSocket) -> None:
        await _serve_websocket(session, websocket)

    return app


async def _serve_websocket(session: RuntimeSession, websocket: WebSocket) -> None:
    """Accept one websocket connection and pump frames in both directions."""
    await websocket.accept()
    sub = session.subscribe()
    send_task = asyncio.create_task(_send_loop(websocket, sub))
    try:
        await _recv_loop(session, websocket)
    except WebSocketDisconnect:
        return
    finally:
        send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, BaseException):
            await send_task
        session.unsubscribe(sub)


async def _recv_loop(session: RuntimeSession, websocket: WebSocket) -> None:
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
            await websocket.send_json({"type": "pong", "payload": {}})
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


async def _send_loop(websocket: WebSocket, sub: Any) -> None:
    while True:
        frame = await sub.queue.get()
        try:
            envelope = encode_frame(frame)
        except WireSerializationError as exc:
            LOGGER.warning("Skipping non-serializable frame %s: %s", type(frame).__name__, exc)
            continue
        try:
            await websocket.send_json(envelope)
        except WebSocketDisconnect:
            return


async def _emit_error(websocket: WebSocket, component: str, reason: str) -> None:
    try:
        await websocket.send_json(
            encode_frame(PipelineErrorFrame(component=component, reason=reason))
        )
    except WebSocketDisconnect:
        return
