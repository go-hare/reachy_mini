"""High-level remote bridge session built on the unified transport layer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .messaging import BridgeMessage, MessageType
from .transport import BridgeTransportOptions, create_bridge_transport


@dataclass(slots=True)
class RemoteBridgeSessionConfig:
    base_url: str
    auth_token: str
    session_id: str
    mode: str = "auto"
    poll_interval: float = 1.0
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 30.0
    liveness_timeout: float = 45.0
    signaling_url: str = ""
    ice_servers: list[dict[str, Any]] | None = None


class RemoteBridgeSession:
    """Unified remote bridge session wrapper for query/event workflows."""

    def __init__(self, config: RemoteBridgeSessionConfig) -> None:
        self._config = config
        self._transport = create_bridge_transport(
            BridgeTransportOptions(
                base_url=config.base_url,
                auth_token=config.auth_token,
                session_id=config.session_id,
                mode=config.mode,
                poll_interval=config.poll_interval,
                reconnect_base_delay=config.reconnect_base_delay,
                reconnect_max_delay=config.reconnect_max_delay,
                liveness_timeout=config.liveness_timeout,
                signaling_url=config.signaling_url,
                ice_servers=config.ice_servers,
            )
        )
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._started = False

    @property
    def transport(self) -> Any:
        return self._transport

    @property
    def session_id(self) -> str:
        return self._config.session_id

    async def start(self) -> None:
        if self._started:
            return
        if hasattr(self._transport, "start"):
            self._transport.start(self._events.put_nowait)
            state_attr = getattr(self._transport, "state", None)
            if state_attr is not None:
                deadline = asyncio.get_running_loop().time() + 10.0
                connected = False
                while asyncio.get_running_loop().time() < deadline:
                    state_value = str(getattr(self._transport, "state", "")).lower()
                    if state_value.endswith("connected"):
                        connected = True
                        break
                    if state_value.endswith("closed"):
                        raise RuntimeError("Bridge transport closed during startup")
                    await asyncio.sleep(0.05)
                if not connected:
                    raise RuntimeError("Bridge transport did not reach connected state before timeout")
        elif hasattr(self._transport, "connect"):
            await self._transport.connect()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        if hasattr(self._transport, "close"):
            result = self._transport.close()
            if asyncio.iscoroutine(result):
                await result
        self._started = False

    async def query(self, text: str) -> Any:
        """Send a remote query using the active transport."""
        if hasattr(self._transport, "send_message"):
            return await self._transport.send_message(
                BridgeMessage(
                    type=MessageType.QUERY,
                    payload={"text": text},
                    session_id=self._config.session_id,
                )
            )
        raise RuntimeError("Active transport does not support send_message")

    async def tool_call(self, tool_name: str, tool_input: dict[str, Any] | None = None) -> Any:
        """Invoke a remote tool using the active transport."""
        if hasattr(self._transport, "send_message"):
            return await self._transport.send_message(
                BridgeMessage(
                    type=MessageType.TOOL_CALL,
                    payload={
                        "tool_name": tool_name,
                        "tool_input": tool_input or {},
                    },
                    session_id=self._config.session_id,
                )
            )
        raise RuntimeError("Active transport does not support send_message")

    async def next_event(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Wait for the next replayed or streamed bridge event."""
        if timeout is None:
            return await self._events.get()
        return await asyncio.wait_for(self._events.get(), timeout=timeout)

    async def drain_events(self) -> list[dict[str, Any]]:
        """Drain all currently buffered bridge events."""
        events: list[dict[str, Any]] = []
        while not self._events.empty():
            events.append(self._events.get_nowait())
        return events
