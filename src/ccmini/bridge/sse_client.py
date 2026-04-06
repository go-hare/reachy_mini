"""SSE bridge client with reconnect and sequence-based replay."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class SSEBridgeClientState(str, Enum):
    IDLE = "idle"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


@dataclass(slots=True)
class SSEBridgeClientStats:
    reconnect_attempts: int = 0
    last_error: str = ""
    last_event_at: float = 0.0


class SSEBridgeClient:
    """Read bridge events from the SSE stream endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        auth_token: str,
        session_id: str,
        since: int = 0,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
        liveness_timeout: float = 45.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._session_id = session_id
        self._last_sequence_num = since
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._liveness_timeout = liveness_timeout

        self._state = SSEBridgeClientState.IDLE
        self._task: asyncio.Task[None] | None = None
        self._stats = SSEBridgeClientStats()
        self._stop = asyncio.Event()

    @property
    def state(self) -> SSEBridgeClientState:
        return self._state

    @property
    def last_sequence_num(self) -> int:
        return self._last_sequence_num

    @property
    def stats(self) -> SSEBridgeClientStats:
        return self._stats

    def start(self, on_event: Callable[[dict[str, Any]], Any]) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(on_event))

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._state = SSEBridgeClientState.CLOSED

    async def _run(self, on_event: Callable[[dict[str, Any]], Any]) -> None:
        backoff = self._reconnect_base_delay
        while not self._stop.is_set():
            try:
                await self._connect_once(on_event)
                self._stats.reconnect_attempts = 0
                backoff = self._reconnect_base_delay
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._stats.last_error = str(exc)
                self._stats.reconnect_attempts += 1
                self._state = SSEBridgeClientState.RECONNECTING
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max_delay)

    async def _connect_once(self, on_event: Callable[[dict[str, Any]], Any]) -> None:
        import aiohttp

        self._state = SSEBridgeClientState.RECONNECTING
        query = urlencode({"session_id": self._session_id, "since": self._last_sequence_num})
        headers = {"Authorization": f"Bearer {self._auth_token}"}
        url = f"{self._base_url}/bridge/events/stream?{query}"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=None, sock_read=self._liveness_timeout),
            ) as response:
                if response.status in {401, 403, 404}:
                    self._state = SSEBridgeClientState.CLOSED
                    raise RuntimeError(f"SSE bridge permanently rejected: HTTP {response.status}")
                if response.status >= 400:
                    raise RuntimeError(f"SSE bridge error: HTTP {response.status}")

                self._state = SSEBridgeClientState.CONNECTED
                event_name = ""
                event_id = ""
                data_lines: list[str] = []
                while not self._stop.is_set():
                    raw = await response.content.readline()
                    if not raw:
                        raise RuntimeError("SSE stream ended")
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    self._stats.last_event_at = time.time()
                    if line.startswith(":"):
                        continue
                    if line == "":
                        if event_name == "client_event" and data_lines:
                            payload = json.loads("\n".join(data_lines))
                            seq = int(payload.get("sequence_num", event_id or 0) or 0)
                            if seq > self._last_sequence_num:
                                self._last_sequence_num = seq
                            maybe = on_event(payload)
                            if asyncio.iscoroutine(maybe):
                                await maybe
                        event_name = ""
                        event_id = ""
                        data_lines = []
                        continue
                    if line.startswith("event:"):
                        event_name = line.split(":", 1)[1].strip()
                    elif line.startswith("id:"):
                        event_id = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line.split(":", 1)[1].lstrip())
