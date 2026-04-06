"""Polling bridge client with sequence-based event replay and reconnect."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlencode

from .messaging import BridgeMessage, MessageType, decode, encode

logger = logging.getLogger(__name__)


class BridgeClientState(str, Enum):
    IDLE = "idle"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


RequestImpl = Callable[
    [str, str, dict[str, str], bytes | None],
    Awaitable[tuple[int, dict[str, Any]]],
]


class BridgeClientHTTPError(RuntimeError):
    """HTTP error from the bridge client."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _is_permanent_status(status: int) -> bool:
    return status in {401, 403, 404}


@dataclass(slots=True)
class BridgeClientStats:
    reconnect_attempts: int = 0
    last_success_at: float = 0.0
    last_error: str = ""


class BridgeClient:
    """Minimal remote bridge client using polling + replay."""

    def __init__(
        self,
        *,
        base_url: str,
        auth_token: str,
        session_id: str,
        poll_interval: float = 1.0,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
        liveness_timeout: float = 45.0,
        request_impl: RequestImpl | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._session_id = session_id
        self._poll_interval = poll_interval
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._liveness_timeout = liveness_timeout
        self._request_impl = request_impl

        self._state = BridgeClientState.IDLE
        self._last_sequence_num = 0
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._stats = BridgeClientStats()

    @property
    def state(self) -> BridgeClientState:
        return self._state

    @property
    def last_sequence_num(self) -> int:
        return self._last_sequence_num

    @property
    def stats(self) -> BridgeClientStats:
        return self._stats

    async def connect(self) -> None:
        """Probe bridge availability and mark the client connected."""
        status, payload = await self._request("GET", "/bridge/status")
        if status >= 400:
            error = payload.get("error", f"HTTP {status}")
            self._stats.last_error = error
            if _is_permanent_status(status):
                self._state = BridgeClientState.CLOSED
            raise BridgeClientHTTPError(status, error)
        self._state = BridgeClientState.CONNECTED
        self._stats.last_success_at = time.time()
        self._stats.last_error = ""

    async def close(self) -> None:
        """Stop the polling loop and close the client."""
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._state = BridgeClientState.CLOSED

    async def send_message(self, message: BridgeMessage) -> BridgeMessage:
        """Send a bridge message and decode the bridge response."""
        body = encode(message).encode("utf-8")
        status, payload = await self._request("POST", "/bridge/message", body=body)
        if status >= 400:
            raise BridgeClientHTTPError(status, payload.get("error", f"HTTP {status}"))
        return decode_json_message(payload)

    async def poll_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch bridge events newer than the last seen sequence number."""
        query = urlencode(
            {
                "session_id": self._session_id,
                "since": self._last_sequence_num,
                "limit": limit,
            }
        )
        status, payload = await self._request("GET", f"/bridge/events?{query}")
        if status >= 400:
            raise BridgeClientHTTPError(status, payload.get("error", f"HTTP {status}"))

        events = payload.get("payload", {}).get("events", [])
        if not isinstance(events, list):
            return []
        for event in events:
            seq = int(event.get("sequence_num", 0) or 0)
            if seq > self._last_sequence_num:
                self._last_sequence_num = seq
        self._stats.last_success_at = time.time()
        self._stats.last_error = ""
        return events

    def start(self, on_event: Callable[[dict[str, Any]], Any]) -> None:
        """Start background polling with reconnect and liveness handling."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._poll_loop(on_event))

    async def _poll_loop(self, on_event: Callable[[dict[str, Any]], Any]) -> None:
        backoff = self._reconnect_base_delay
        while not self._stop_event.is_set():
            try:
                if self._state in {BridgeClientState.IDLE, BridgeClientState.RECONNECTING}:
                    await self.connect()

                events = await self.poll_events()
                self._state = BridgeClientState.CONNECTED
                self._stats.reconnect_attempts = 0
                backoff = self._reconnect_base_delay

                for event in events:
                    maybe = on_event(event)
                    if asyncio.iscoroutine(maybe):
                        await maybe

                if self._stats.last_success_at and (time.time() - self._stats.last_success_at) > self._liveness_timeout:
                    raise RuntimeError("Bridge liveness timeout")

                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._stats.last_error = str(exc)
                self._stats.reconnect_attempts += 1
                if isinstance(exc, BridgeClientHTTPError) and _is_permanent_status(exc.status):
                    self._state = BridgeClientState.CLOSED
                    logger.warning("Bridge client permanently closed: %s", exc)
                    return
                self._state = BridgeClientState.RECONNECTING
                logger.debug("Bridge client reconnecting after error: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max_delay)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Authorization": f"Bearer {self._auth_token}"}
        if self._request_impl is not None:
            return await self._request_impl(method, f"{self._base_url}{path}", headers, body)

        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method,
                f"{self._base_url}{path}",
                data=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                try:
                    payload = await response.json()
                except Exception:
                    payload = {}
                return response.status, payload


def decode_json_message(payload: dict[str, Any]) -> BridgeMessage:
    """Convert a decoded JSON payload into a BridgeMessage."""
    return BridgeMessage(
        type=MessageType(payload.get("type", "error")),
        payload=payload.get("payload", {}),
        session_id=str(payload.get("session_id", "")),
        timestamp=float(payload.get("timestamp", 0.0) or 0.0),
        request_id=str(payload.get("request_id", "")),
        sequence_num=int(payload.get("sequence_num", 0) or 0),
    )
