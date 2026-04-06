"""Optional client-side WebRTC DataChannel transport."""

from __future__ import annotations

import asyncio
from typing import Any

from .messaging import BridgeMessage, MessageType, decode, encode
from .signaling import SignalingAction, SignalingKind, SignalingRole
from .webrtc_transport import (
    WebRTCBridgeClient,
    WebRTCBridgeClientState,
)


class ImplementedWebRTCBridgeClient(WebRTCBridgeClient):
    """Actual aiortc-backed transport; used only when aiortc is installed."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self._pc: Any | None = None
        self._channel: Any | None = None
        self._pending: dict[str, asyncio.Future[BridgeMessage]] = {}
        self._last_signal_seq = 0

    async def send_message(self, message: BridgeMessage) -> BridgeMessage:
        if self._channel is None or getattr(self._channel, "readyState", "") != "open":
            raise RuntimeError("WebRTC data channel is not connected")
        future: asyncio.Future[BridgeMessage] = asyncio.get_running_loop().create_future()
        self._pending[message.request_id] = future
        self._channel.send(encode(message))
        return await future

    async def _run(self) -> None:
        from aiortc import (
            RTCConfiguration,
            RTCIceServer,
            RTCPeerConnection,
        )

        if not self.signal.signaling_url:
            self._state = WebRTCBridgeClientState.CLOSED
            raise RuntimeError("WebRTC bridge transport requires a signaling_url.")

        self._state = WebRTCBridgeClientState.CONNECTING
        rtc_ice_servers = [
            RTCIceServer(**server)
            for server in self.ice_servers
            if isinstance(server, dict)
        ]
        self._pc = RTCPeerConnection(
            RTCConfiguration(iceServers=rtc_ice_servers)
        )
        channel = self._pc.createDataChannel(self.signal.channel_label)
        self._channel = channel

        @channel.on("message")
        def _on_message(raw: str | bytes) -> None:
            asyncio.create_task(self._handle_message(raw))

        @self._pc.on("icecandidate")
        async def _on_icecandidate(candidate: Any) -> None:
            if candidate is None:
                return
            await self._signal_publish(
                kind=SignalingKind.ICE_CANDIDATE,
                recipient=SignalingRole.EXECUTOR,
                data={
                    "candidate": candidate.to_sdp(),
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                },
            )

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        await self._signal_publish(
            kind=SignalingKind.OFFER,
            recipient=SignalingRole.EXECUTOR,
            data={
                "sdp": self._pc.localDescription.sdp,
                "type": self._pc.localDescription.type,
            },
        )

        while not self._stop.is_set():
            await self._poll_signaling()
            if channel.readyState == "open":
                self._state = WebRTCBridgeClientState.CONNECTED
            await asyncio.sleep(0.2)

    async def _handle_message(self, raw: str | bytes) -> None:
        message = decode(raw)
        if message.type is MessageType.EVENTS:
            events = message.payload.get("events", [])
            if isinstance(events, list):
                for event in events:
                    if self._on_event is not None:
                        maybe = self._on_event(event)
                        if asyncio.iscoroutine(maybe):
                            await maybe
        future = self._pending.pop(message.request_id, None)
        if future is not None and not future.done():
            future.set_result(message)

    async def _poll_signaling(self) -> None:
        from aiortc import RTCSessionDescription
        from aiortc.sdp import candidate_from_sdp

        payload = await self._signal_request(
            {
                "action": SignalingAction.FETCH.value,
                "recipient": SignalingRole.CLIENT.value,
                "since": self._last_signal_seq,
                "limit": 100,
            }
        )
        messages = payload.get("messages", [])
        for item in messages if isinstance(messages, list) else []:
            seq = int(item.get("sequence_num", 0) or 0)
            self._last_signal_seq = max(self._last_signal_seq, seq)
            kind = str(item.get("kind", "")).strip().lower()
            data = item.get("data", {}) if isinstance(item.get("data", {}), dict) else {}
            if kind == SignalingKind.ANSWER.value:
                if self._pc is not None and self._pc.remoteDescription is None:
                    await self._pc.setRemoteDescription(
                        RTCSessionDescription(
                            sdp=str(data.get("sdp", "")).strip(),
                            type=str(data.get("type", "answer")).strip() or "answer",
                        )
                    )
            elif kind == SignalingKind.ICE_CANDIDATE.value:
                if self._pc is not None:
                    candidate = str(data.get("candidate", "")).strip()
                    if candidate:
                        parsed = candidate_from_sdp(candidate)
                        parsed.sdpMid = data.get("sdpMid")
                        parsed.sdpMLineIndex = data.get("sdpMLineIndex")
                        await self._pc.addIceCandidate(parsed)

    async def _signal_publish(
        self,
        *,
        kind: SignalingKind,
        recipient: SignalingRole,
        data: dict[str, Any],
    ) -> None:
        await self._signal_request(
            {
                "action": SignalingAction.PUBLISH.value,
                "sender": SignalingRole.CLIENT.value,
                "recipient": recipient.value,
                "kind": kind.value,
                "data": data,
            }
        )

    async def _signal_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        import aiohttp

        body = encode(
            BridgeMessage(
                type=MessageType.SIGNALING,
                payload=payload,
                session_id=self.signal.session_id,
            )
        ).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.signal.auth_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.signal.signaling_url.rstrip('/')}/bridge/message",
                data=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                payload = await response.json()
                if response.status >= 400:
                    raise RuntimeError(payload.get("error", f"HTTP {response.status}"))
                return payload.get("payload", {})
