"""Remote-control bridge — exposes a running agent to external clients.

Ported from Claude Code's ``bridge/`` subsystem:
- ``core`` — WebSocket / HTTP bridge server with auth
- ``api`` — session-level request handlers
- ``messaging`` — typed message protocol with JSON serialization
"""

from .api import BridgeAPI
from .client import BridgeClient, BridgeClientState
from .core import BridgeConfig, BridgeServer
from .host import (
    RemoteExecutorHost,
    RemoteExecutorSessionHandle,
    create_remote_executor_host,
)
from .messaging import (
    BridgeMessage,
    MessageType,
    decode,
    encode,
    validate_message,
)
from .signaling import (
    SignalingAction,
    SignalingKind,
    SignalingMessage,
    SignalingRole,
)
from .session import RemoteBridgeSession, RemoteBridgeSessionConfig
from .sse_client import SSEBridgeClient, SSEBridgeClientState
from .transport import BridgeTransportOptions, create_bridge_transport
from .webrtc_transport import (
    WebRTCBridgeClient,
    WebRTCBridgeClientState,
    WebRTCBridgeClientStats,
    WebRTCSignalConfig,
)
from .ws_client import WebSocketBridgeClient, WSBridgeClientState

__all__ = [
    "BridgeAPI",
    "BridgeClient",
    "BridgeClientState",
    "BridgeConfig",
    "BridgeMessage",
    "BridgeServer",
    "RemoteExecutorHost",
    "RemoteExecutorSessionHandle",
    "create_remote_executor_host",
    "BridgeTransportOptions",
    "MessageType",
    "SignalingAction",
    "SignalingKind",
    "SignalingMessage",
    "SignalingRole",
    "RemoteBridgeSession",
    "RemoteBridgeSessionConfig",
    "SSEBridgeClient",
    "SSEBridgeClientState",
    "WebRTCBridgeClient",
    "WebRTCBridgeClientState",
    "WebRTCBridgeClientStats",
    "WebRTCSignalConfig",
    "WebSocketBridgeClient",
    "WSBridgeClientState",
    "create_bridge_transport",
    "decode",
    "encode",
    "validate_message",
]
