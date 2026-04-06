"""WebSocket bridge client with replay-aware event polling."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .messaging import BridgeMessage, MessageType, decode, encode

logger = logging.getLogger(__name__)


class WSBridgeClientState(str, Enum):
    IDLE = "idle"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


@dataclass(slots=True)
class WSBridgeClientStats:
    reconnect_attempts: int = 0
    last_error: str = ""


class WebSocketBridgeClient:
    """WebSocket bridge client with periodic replay polling over WS."""

    def __init__(
        self,
        *,
        url: str,
        auth_token: str,
        session_id: str,
        poll_interval: float = 1.0,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
    ) -> None:
        self._url = url
        self._auth_token = auth_token
        self._session_id = session_id
        self._poll_interval = poll_interval
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay

        self._state = WSBridgeClientState.IDLE
        self._last_sequence_num = 0
        self._pending: dict[str, asyncio.Future[BridgeMessage]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._ws: Any | None = None
        self._on_event: Callable[[dict[str, Any]], Any] | None = None
        self._stats = WSBridgeClientStats()

    @property
    def state(self) -> WSBridgeClientState:
        return self._state

    @property
    def last_sequence_num(self) -> int:
        return self._last_sequence_num

    @property
    def stats(self) -> WSBridgeClientStats:
        return self._stats

    def start(self, on_event: Callable[[dict[str, Any]], Any]) -> None:
        if self._task is not None and not self._task.done():
            return
        self._on_event = on_event
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._state = WSBridgeClientState.CLOSED
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    async def send_message(self, message: BridgeMessage) -> BridgeMessage:
        if self._ws is None:
            raise RuntimeError("WebSocket bridge is not connected")
        future: asyncio.Future[BridgeMessage] = asyncio.get_running_loop().create_future()
        self._pending[message.request_id] = future
        async with self._send_lock:
            await self._ws.send(encode(message))
        return await future

    async def _run(self) -> None:
        import websockets

        backoff = self._reconnect_base_delay
        while not self._stop.is_set():
            try:
                self._state = WSBridgeClientState.RECONNECTING
                async with websockets.connect(self._url) as ws:
                    self._ws = ws
                    await ws.send(
                        json.dumps(
                            {
                                "auth_token": self._auth_token,
                                "session_id": self._session_id,
                            }
                        )
                    )
                    auth_reply = json.loads(await ws.recv())
                    if auth_reply.get("status") != "authenticated":
                        self._state = WSBridgeClientState.CLOSED
                        raise RuntimeError("WebSocket bridge authentication failed")
                    server_session_id = str(auth_reply.get("session_id", "")).strip()
                    if server_session_id:
                        self._session_id = server_session_id

                    self._state = WSBridgeClientState.CONNECTED
                    recv_task = asyncio.create_task(self._recv_loop())
                    try:
                        while not self._stop.is_set():
                            await self._request_events()
                            await asyncio.sleep(self._poll_interval)
                    finally:
                        recv_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await recv_task
                self._stats.reconnect_attempts = 0
                backoff = self._reconnect_base_delay
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._stats.last_error = str(exc)
                self._stats.reconnect_attempts += 1
                if "authentication failed" in str(exc).lower():
                    self._state = WSBridgeClientState.CLOSED
                    return
                self._state = WSBridgeClientState.RECONNECTING
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max_delay)

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            message = decode(raw)
            if message.type is MessageType.HEARTBEAT:
                continue
            if message.type is MessageType.EVENTS:
                events = message.payload.get("events", [])
                if isinstance(events, list):
                    for event in events:
                        seq = int(event.get("sequence_num", 0) or 0)
                        if seq > self._last_sequence_num:
                            self._last_sequence_num = seq
                        if self._on_event is not None:
                            maybe = self._on_event(event)
                            if asyncio.iscoroutine(maybe):
                                await maybe
            future = self._pending.pop(message.request_id, None)
            if future is not None and not future.done():
                future.set_result(message)

    async def _request_events(self) -> None:
        request = BridgeMessage(
            type=MessageType.EVENTS,
            payload={"since": self._last_sequence_num, "limit": 100},
            session_id=self._session_id,
        )
        await self.send_message(request)
