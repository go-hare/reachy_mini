"""Optional executor-side WebRTC peer manager."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from .api import BridgeAPI
from .messaging import BridgeMessage, MessageType, decode, encode
from .signaling import SignalingKind, SignalingRole

logger = logging.getLogger(__name__)


def _has_aiortc() -> bool:
    try:
        import aiortc  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass(slots=True)
class _PeerState:
    session_id: str
    pc: Any
    channel: Any | None = None
    signaling_seq: int = 0
    task: asyncio.Task[None] | None = None


class WebRTCExecutorManager:
    """Consumes signaling offers and serves bridge messages over DataChannel."""

    def __init__(self, api: BridgeAPI) -> None:
        self._api = api
        self._peers: dict[str, _PeerState] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return _has_aiortc()

    def start(self) -> None:
        if not self.enabled:
            logger.info("WebRTC executor manager disabled: aiortc not installed")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for state in list(self._peers.values()):
            await self._close_peer(state)
        self._peers.clear()

    async def push_event(self, session_id: str, event: dict[str, Any]) -> None:
        state = self._peers.get(session_id)
        if state is None or state.channel is None:
            return
        try:
            state.channel.send(
                encode(
                    BridgeMessage(
                        type=MessageType.EVENTS,
                        payload={"events": [event]},
                        session_id=session_id,
                    )
                )
            )
        except Exception:
            logger.debug("Failed to push WebRTC event", exc_info=True)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                for session_id in list(self._api._sessions.keys()):  # noqa: SLF001
                    await self._process_signals(session_id)
                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("WebRTC executor loop error", exc_info=True)
                await asyncio.sleep(0.5)

    async def _process_signals(self, session_id: str) -> None:
        state = self._peers.get(session_id)
        since = state.signaling_seq if state is not None else 0
        try:
            messages = self._api.get_signals(
                session_id,
                recipient=SignalingRole.EXECUTOR,
                since=since,
                limit=100,
            )
        except Exception:
            return
        for item in messages:
            seq = int(item.get("sequence_num", 0) or 0)
            if state is None:
                state = await self._ensure_peer(session_id)
            state.signaling_seq = max(state.signaling_seq, seq)
            kind = str(item.get("kind", "")).strip().lower()
            data = item.get("data", {}) if isinstance(item.get("data", {}), dict) else {}
            if kind == SignalingKind.OFFER.value:
                await self._apply_offer(state, data)
            elif kind == SignalingKind.ICE_CANDIDATE.value:
                await self._apply_ice_candidate(state, data)
            elif kind == SignalingKind.BYE.value:
                await self._close_peer(state)
                self._peers.pop(session_id, None)
                state = None

    async def _ensure_peer(self, session_id: str) -> _PeerState:
        state = self._peers.get(session_id)
        if state is not None:
            return state

        from aiortc import RTCPeerConnection

        pc = RTCPeerConnection()
        state = _PeerState(session_id=session_id, pc=pc)
        self._peers[session_id] = state

        @pc.on("datachannel")
        def _on_datachannel(channel: Any) -> None:
            state.channel = channel

            @channel.on("message")
            def _on_message(raw: str | bytes) -> None:
                asyncio.create_task(self._handle_channel_message(state, raw))

        @pc.on("icecandidate")
        async def _on_icecandidate(candidate: Any) -> None:
            if candidate is None:
                return
            self._api.publish_signal(
                session_id,
                sender=SignalingRole.EXECUTOR,
                recipient=SignalingRole.CLIENT,
                kind=SignalingKind.ICE_CANDIDATE,
                data={
                    "candidate": candidate.to_sdp(),
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                },
            )

        @pc.on("connectionstatechange")
        async def _on_connectionstatechange() -> None:
            if getattr(pc, "connectionState", "") in {"failed", "closed"}:
                await self._close_peer(state)
                self._peers.pop(session_id, None)

        return state

    async def _apply_offer(self, state: _PeerState, data: dict[str, Any]) -> None:
        from aiortc import RTCSessionDescription

        sdp = str(data.get("sdp", "")).strip()
        offer_type = str(data.get("type", "offer")).strip() or "offer"
        await state.pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp, type=offer_type)
        )
        answer = await state.pc.createAnswer()
        await state.pc.setLocalDescription(answer)
        self._api.publish_signal(
            state.session_id,
            sender=SignalingRole.EXECUTOR,
            recipient=SignalingRole.CLIENT,
            kind=SignalingKind.ANSWER,
            data={
                "sdp": state.pc.localDescription.sdp,
                "type": state.pc.localDescription.type,
            },
        )

    async def _apply_ice_candidate(self, state: _PeerState, data: dict[str, Any]) -> None:
        from aiortc.sdp import candidate_from_sdp

        candidate = str(data.get("candidate", "")).strip()
        if not candidate:
            return
        parsed = candidate_from_sdp(candidate)
        parsed.sdpMid = data.get("sdpMid")
        parsed.sdpMLineIndex = data.get("sdpMLineIndex")
        await state.pc.addIceCandidate(parsed)

    async def _handle_channel_message(self, state: _PeerState, raw: str | bytes) -> None:
        try:
            message = decode(raw)
            message.session_id = message.session_id or state.session_id
            response = await self._api.handle_message(state.session_id, message)
            if state.channel is not None:
                state.channel.send(encode(response))
        except Exception as exc:
            if state.channel is not None:
                state.channel.send(
                    encode(
                        BridgeMessage(
                            type=MessageType.ERROR,
                            payload={"error": str(exc)},
                            session_id=state.session_id,
                        )
                    )
                )

    async def _close_peer(self, state: _PeerState) -> None:
        try:
            await state.pc.close()
        except Exception:
            pass
