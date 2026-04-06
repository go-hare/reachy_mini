"""Unified transport selection for bridge clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .client import BridgeClient
from .sse_client import SSEBridgeClient
from .webrtc_transport import WebRTCBridgeClient, WebRTCSignalConfig
from .ws_client import WebSocketBridgeClient


@dataclass(slots=True)
class BridgeTransportOptions:
    base_url: str
    auth_token: str
    session_id: str
    mode: str = "auto"  # auto | polling | sse | ws | webrtc
    poll_interval: float = 1.0
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 30.0
    liveness_timeout: float = 45.0
    signaling_url: str = ""
    ice_servers: list[dict[str, Any]] | None = None


def create_bridge_transport(options: BridgeTransportOptions) -> Any:
    """Create the appropriate bridge client for the requested mode."""
    mode = options.mode.lower()
    if mode == "auto":
        parsed = urlparse(options.base_url)
        if parsed.scheme in {"ws", "wss"}:
            mode = "ws"
        else:
            mode = "sse"

    if mode == "ws":
        ws_url = options.base_url
        if ws_url.startswith("http://"):
            ws_url = "ws://" + ws_url[len("http://"):]
        elif ws_url.startswith("https://"):
            ws_url = "wss://" + ws_url[len("https://"):]
        return WebSocketBridgeClient(
            url=ws_url,
            auth_token=options.auth_token,
            session_id=options.session_id,
            poll_interval=options.poll_interval,
            reconnect_base_delay=options.reconnect_base_delay,
            reconnect_max_delay=options.reconnect_max_delay,
        )

    if mode == "webrtc":
        client_cls = WebRTCBridgeClient
        try:
            from .webrtc_transport_impl import ImplementedWebRTCBridgeClient

            client_cls = ImplementedWebRTCBridgeClient
        except Exception:
            pass
        signaling_url = options.signaling_url or options.base_url
        return client_cls(
            signal=WebRTCSignalConfig(
                signaling_url=signaling_url,
                auth_token=options.auth_token,
                session_id=options.session_id,
            ),
            ice_servers=list(options.ice_servers or []),
            reconnect_base_delay=options.reconnect_base_delay,
            reconnect_max_delay=options.reconnect_max_delay,
        )

    if mode == "sse":
        return SSEBridgeClient(
            base_url=options.base_url,
            auth_token=options.auth_token,
            session_id=options.session_id,
            since=0,
            reconnect_base_delay=options.reconnect_base_delay,
            reconnect_max_delay=options.reconnect_max_delay,
            liveness_timeout=options.liveness_timeout,
        )

    return BridgeClient(
        base_url=options.base_url,
        auth_token=options.auth_token,
        session_id=options.session_id,
        poll_interval=options.poll_interval,
        reconnect_base_delay=options.reconnect_base_delay,
        reconnect_max_delay=options.reconnect_max_delay,
        liveness_timeout=options.liveness_timeout,
    )
