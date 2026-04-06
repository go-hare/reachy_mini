"""WebRTC bridge transport skeleton.

This module intentionally provides a thin integration surface first:
- a stable client state model
- signaling/ICE configuration
- the same ``start`` / ``close`` / ``send_message`` shape as other bridge transports

The current environment does not bundle ``aiortc``, so the transport fails
gracefully with an explicit runtime error until the dependency and signaling
server are wired in.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .messaging import BridgeMessage


class WebRTCBridgeClientState(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


@dataclass(slots=True)
class WebRTCSignalConfig:
    """How the client discovers and negotiates a WebRTC peer connection."""

    signaling_url: str = ""
    auth_token: str = ""
    session_id: str = ""
    channel_label: str = "ccmini-bridge"


@dataclass(slots=True)
class WebRTCBridgeClientStats:
    reconnect_attempts: int = 0
    last_error: str = ""
    signaling_round_trips: int = 0


@dataclass(slots=True)
class WebRTCBridgeClient:
    """WebRTC DataChannel transport skeleton for remote bridge sessions."""

    signal: WebRTCSignalConfig
    ice_servers: list[dict[str, Any]] = field(default_factory=list)
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 30.0

    def __post_init__(self) -> None:
        self._state = WebRTCBridgeClientState.IDLE
        self._stats = WebRTCBridgeClientStats()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._on_event: Callable[[dict[str, Any]], Any] | None = None

    @property
    def state(self) -> WebRTCBridgeClientState:
        return self._state

    @property
    def stats(self) -> WebRTCBridgeClientStats:
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
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._state = WebRTCBridgeClientState.CLOSED

    async def send_message(self, message: BridgeMessage) -> BridgeMessage:
        raise RuntimeError(
            "WebRTC bridge transport is not active yet. "
            "Install aiortc and complete signaling integration before using mode='webrtc'."
        )

    async def _run(self) -> None:
        self._state = WebRTCBridgeClientState.CONNECTING
        try:
            import aiortc  # noqa: F401
        except ImportError as exc:
            self._stats.last_error = str(exc)
            self._state = WebRTCBridgeClientState.CLOSED
            raise RuntimeError(
                "WebRTC bridge transport requires the optional dependency 'aiortc'."
            ) from exc

        if not self.signal.signaling_url:
            self._state = WebRTCBridgeClientState.CLOSED
            raise RuntimeError(
                "WebRTC bridge transport requires a signaling_url."
            )

        # Placeholder for future implementation:
        # 1. Create RTCPeerConnection
        # 2. Open DataChannel with ``channel_label``
        # 3. Exchange offer/answer over signaling_url
        # 4. Forward inbound DataChannel messages to ``self._on_event``
        self._state = WebRTCBridgeClientState.CLOSED
        raise RuntimeError(
            "WebRTC bridge signaling is not implemented yet. "
            "Use mode='ws', 'sse', or 'polling' for now."
        )
