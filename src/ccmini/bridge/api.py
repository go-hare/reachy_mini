"""Bridge API — session-level request handlers for the remote-control bridge.

Ported from Claude Code's ``bridge/bridgeApi.ts``:
- ``BridgeAPI`` handles messages, queries, tool calls, and session management
- Session isolation: each session has its own message history
- Validates IDs against an allowlist pattern (prevents path traversal)

All methods are async — callers in ``core.py`` await them from the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .messaging import BridgeMessage, MessageType, make_error, make_events, make_response
from .signaling import (
    SignalingAction,
    SignalingKind,
    SignalingMessage,
    SignalingRole,
    parse_signaling_kind,
    parse_signaling_role,
)

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_id(value: str, label: str) -> str:
    """Validate that a session/request ID is safe to use."""
    if not value or not _SAFE_ID.match(value):
        raise ValueError(f"Invalid {label}: contains unsafe characters")
    return value


# ── Session tracking ────────────────────────────────────────────────

@dataclass
class SessionInfo:
    """Bookkeeping for a single bridge session."""

    session_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    message_count: int = 0
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    next_sequence_num: int = 1
    event_log: list[dict[str, Any]] = field(default_factory=list)


# ── Bridge API ──────────────────────────────────────────────────────

class BridgeAPI:
    """Session-level request handlers for the bridge.

    Each method corresponds to an action a remote client can invoke.
    The ``BridgeServer`` (core.py) routes incoming messages here.
    """

    def __init__(
        self,
        *,
        on_query: Any = None,
        on_tool_call: Any = None,
        on_submit_tool_results: Any = None,
    ) -> None:
        self._sessions: dict[str, SessionInfo] = {}
        self._signaling_logs: dict[str, list[SignalingMessage]] = {}
        self._on_query = on_query
        self._on_tool_call = on_tool_call
        self._on_submit_tool_results = on_submit_tool_results

    # ── Session management ──────────────────────────────────────────

    def create_session(self, metadata: dict[str, Any] | None = None) -> str:
        """Create a new session and return its ID."""
        sid = uuid.uuid4().hex[:16]
        self._sessions[sid] = SessionInfo(
            session_id=sid, metadata=metadata or {},
        )
        logger.debug("Bridge session created: %s", sid)
        return sid

    def end_session(self, session_id: str) -> bool:
        """Mark a session as ended. Returns True if it existed."""
        info = self._sessions.get(session_id)
        if info is None:
            return False
        info.status = "ended"
        logger.debug("Bridge session ended: %s", session_id)
        return True

    def remove_session(self, session_id: str) -> bool:
        """Remove a session from tracking entirely."""
        return self._sessions.pop(session_id, None) is not None

    # ── Request handlers ────────────────────────────────────────────

    async def handle_message(
        self, session_id: str, message: BridgeMessage,
    ) -> BridgeMessage:
        """Dispatch an incoming ``BridgeMessage`` and return a response."""
        _validate_id(session_id, "session_id")
        info = self._sessions.get(session_id)
        if info is None:
            return make_error(session_id, f"Unknown session: {session_id}")
        if info.status != "active":
            return make_error(session_id, f"Session {session_id} is {info.status}")

        info.last_activity = time.time()
        info.message_count += 1

        if message.type is MessageType.QUERY:
            text = message.payload.get("text", "")
            metadata = message.payload.get("metadata", {})
            attachments = message.payload.get("attachments", [])
            return await self.handle_query(
                session_id,
                text,
                metadata=metadata if isinstance(metadata, dict) else None,
                attachments=attachments if isinstance(attachments, list) else None,
                request_id=message.request_id,
            )
        if message.type is MessageType.TOOL_CALL:
            name = message.payload.get("tool_name", "")
            inp = message.payload.get("tool_input", {})
            return await self.handle_tool_call(
                session_id,
                name,
                inp,
                request_id=message.request_id,
            )
        if message.type is MessageType.SUBMIT_TOOL_RESULTS:
            run_id = str(message.payload.get("run_id", "")).strip()
            results = message.payload.get("results", [])
            return await self.handle_submit_tool_results(
                session_id,
                run_id,
                results if isinstance(results, list) else [],
                request_id=message.request_id,
            )
        if message.type is MessageType.EVENTS:
            since = int(message.payload.get("since", 0) or 0)
            limit = int(message.payload.get("limit", 100) or 100)
            return self.handle_events(
                session_id,
                since=since,
                limit=limit,
                request_id=message.request_id,
            )
        if message.type is MessageType.SIGNALING:
            return self.handle_signaling(
                session_id,
                message.payload,
                request_id=message.request_id,
            )
        if message.type is MessageType.HEARTBEAT:
            return BridgeMessage(type=MessageType.HEARTBEAT, session_id=session_id)

        return make_error(
            session_id,
            f"Unsupported message type: {message.type.value}",
            request_id=message.request_id,
        )

    async def handle_query(
        self,
        session_id: str,
        query_text: str,
        *,
        metadata: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        request_id: str = "",
    ) -> BridgeMessage:
        """Handle a user query and return the response message."""
        _validate_id(session_id, "session_id")

        if self._on_query is not None:
            try:
                try:
                    result = self._on_query(
                        session_id,
                        query_text,
                        metadata=metadata,
                        attachments=attachments,
                    )
                except TypeError:
                    result = self._on_query(session_id, query_text)
                if asyncio.iscoroutine(result):
                    result = await result
                response = make_response(
                    session_id,
                    str(result),
                    request_id=request_id,
                )
                return self._record_event(session_id, response)
            except Exception as exc:
                logger.error("Query handler failed: %s", exc, exc_info=True)
                return self._record_event(
                    session_id,
                    make_error(session_id, str(exc), request_id=request_id),
                )

        return self._record_event(
            session_id,
            make_error(
                session_id,
                "No query handler registered",
                request_id=request_id,
            ),
        )

    async def handle_tool_call(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        request_id: str = "",
    ) -> BridgeMessage:
        """Execute a tool on behalf of a remote client."""
        _validate_id(session_id, "session_id")

        if self._on_tool_call is not None:
            try:
                result = self._on_tool_call(session_id, tool_name, tool_input)
                if asyncio.iscoroutine(result):
                    result = await result
                return self._record_event(session_id, BridgeMessage(
                    type=MessageType.TOOL_RESULT,
                    payload={"tool_name": tool_name, "result": str(result)},
                    session_id=session_id,
                    request_id=request_id,
                ))
            except Exception as exc:
                logger.error("Tool call handler failed: %s", exc, exc_info=True)
                return self._record_event(
                    session_id,
                    make_error(session_id, str(exc), request_id=request_id),
                )

        return self._record_event(
            session_id,
            make_error(
                session_id,
                "No tool call handler registered",
                request_id=request_id,
            ),
        )

    async def handle_submit_tool_results(
        self,
        session_id: str,
        run_id: str,
        results: list[dict[str, Any]],
        *,
        request_id: str = "",
    ) -> BridgeMessage:
        """Submit host-side client tool results and continue the query loop."""
        _validate_id(session_id, "session_id")

        if self._on_submit_tool_results is not None:
            try:
                result = self._on_submit_tool_results(session_id, run_id, results)
                if asyncio.iscoroutine(result):
                    result = await result
                response = make_response(
                    session_id,
                    str(result),
                    request_id=request_id,
                )
                return self._record_event(session_id, response)
            except Exception as exc:
                logger.error("Submit tool results handler failed: %s", exc, exc_info=True)
                return self._record_event(
                    session_id,
                    make_error(session_id, str(exc), request_id=request_id),
                )

        return self._record_event(
            session_id,
            make_error(
                session_id,
                "No submit_tool_results handler registered",
                request_id=request_id,
            ),
        )

    def handle_events(
        self,
        session_id: str,
        *,
        since: int = 0,
        limit: int = 100,
        request_id: str = "",
    ) -> BridgeMessage:
        """Return session events with sequence numbers greater than *since*."""
        events = self.get_events(session_id, since=since, limit=limit)
        if events is None:
            return make_error(
                session_id,
                f"Unknown session: {session_id}",
                request_id=request_id,
            )
        msg = make_events(session_id, events, request_id=request_id)
        info = self._sessions[session_id]
        msg.sequence_num = info.next_sequence_num - 1
        return msg

    def get_events(
        self,
        session_id: str,
        *,
        since: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]] | None:
        """Return raw event records for a session, or None if unknown."""
        info = self._sessions.get(session_id)
        if info is None:
            return None
        events = [
            event for event in info.event_log
            if int(event.get("sequence_num", 0)) > since
        ]
        if limit > 0:
            events = events[:limit]
        return events

    def get_session_status(self, session_id: str) -> dict[str, Any]:
        """Return status dict for a session (or error info)."""
        info = self._sessions.get(session_id)
        if info is None:
            return {"error": f"Unknown session: {session_id}"}
        return {
            "session_id": info.session_id,
            "status": info.status,
            "created_at": info.created_at,
            "last_activity": info.last_activity,
            "message_count": info.message_count,
            "metadata": info.metadata,
            "next_sequence_num": info.next_sequence_num,
            "event_count": len(info.event_log),
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return status dicts for all tracked sessions."""
        return [
            self.get_session_status(sid) for sid in self._sessions
        ]

    def get_runtime_snapshot(self, session_id: str) -> dict[str, Any] | None:
        """Return optional host-provided runtime state for a session."""
        del session_id
        return None

    async def control_runtime_task(
        self,
        session_id: str,
        *,
        task_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del session_id, task_id, action, payload
        return {"ok": False, "error": "Runtime task control is not supported."}

    def get_runtime_transcript(
        self,
        session_id: str,
        *,
        task_id: str,
        limit: int = 200,
    ) -> dict[str, Any]:
        del session_id, task_id, limit
        return {"ok": False, "error": "Runtime transcript access is not supported."}

    def publish_signal(
        self,
        session_id: str,
        *,
        sender: SignalingRole,
        recipient: SignalingRole,
        kind: SignalingKind,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one signaling message for a session and return it."""
        _validate_id(session_id, "session_id")
        if session_id not in self._sessions:
            raise ValueError(f"Unknown session: {session_id}")
        log = self._signaling_logs.setdefault(session_id, [])
        next_seq = (log[-1].sequence_num if log else 0) + 1
        message = SignalingMessage(
            session_id=session_id,
            sender=sender,
            recipient=recipient,
            kind=kind,
            data=dict(data or {}),
            sequence_num=next_seq,
        )
        log.append(message)
        if len(log) > 1000:
            self._signaling_logs[session_id] = log[-1000:]
        self._sessions[session_id].last_activity = time.time()
        return message.to_dict()

    def get_signals(
        self,
        session_id: str,
        *,
        recipient: SignalingRole,
        since: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch signaling messages addressed to a recipient."""
        _validate_id(session_id, "session_id")
        if session_id not in self._sessions:
            raise ValueError(f"Unknown session: {session_id}")
        log = self._signaling_logs.get(session_id, [])
        items = [
            message.to_dict()
            for message in log
            if message.recipient == recipient and message.sequence_num > since
        ]
        if limit > 0:
            items = items[:limit]
        return items

    def handle_signaling(
        self,
        session_id: str,
        payload: dict[str, Any],
        *,
        request_id: str = "",
    ) -> BridgeMessage:
        """Handle WebRTC signaling publish/fetch over the bridge protocol."""
        _validate_id(session_id, "session_id")
        if session_id not in self._sessions:
            return make_error(
                session_id,
                f"Unknown session: {session_id}",
                request_id=request_id,
            )

        try:
            action = SignalingAction(str(payload.get("action", "")).strip().lower())
        except Exception:
            return make_error(
                session_id,
                "Invalid signaling action",
                request_id=request_id,
            )

        try:
            if action is SignalingAction.PUBLISH:
                sender = parse_signaling_role(str(payload.get("sender", "")))
                recipient = parse_signaling_role(str(payload.get("recipient", "")))
                kind = parse_signaling_kind(str(payload.get("kind", "")))
                item = self.publish_signal(
                    session_id,
                    sender=sender,
                    recipient=recipient,
                    kind=kind,
                    data=payload.get("data", {}),
                )
                return self._record_event(
                    session_id,
                    BridgeMessage(
                        type=MessageType.SIGNALING,
                        payload={"action": "publish", "signal": item},
                        session_id=session_id,
                        request_id=request_id,
                    ),
                )

            recipient = parse_signaling_role(str(payload.get("recipient", "")))
            since = int(payload.get("since", 0) or 0)
            limit = int(payload.get("limit", 100) or 100)
            messages = self.get_signals(
                session_id,
                recipient=recipient,
                since=since,
                limit=limit,
            )
            return self._record_event(
                session_id,
                BridgeMessage(
                    type=MessageType.SIGNALING,
                    payload={"action": "fetch", "messages": messages},
                    session_id=session_id,
                    request_id=request_id,
                ),
            )
        except Exception as exc:
            return self._record_event(
                session_id,
                make_error(session_id, str(exc), request_id=request_id),
            )

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        request_id: str = "",
        timestamp: float | None = None,
    ) -> dict[str, Any] | None:
        """Append a non-BridgeMessage event to a session's event log.

        This is intended for streaming executor-side events that should be
        replayable via ``/bridge/events`` and SSE/WS clients.
        """
        info = self._sessions.get(session_id)
        if info is None:
            return None
        event = {
            "sequence_num": info.next_sequence_num,
            "type": event_type,
            "payload": payload,
            "timestamp": time.time() if timestamp is None else timestamp,
            "request_id": request_id,
        }
        info.next_sequence_num += 1
        info.event_log.append(event)
        if len(info.event_log) > 1000:
            info.event_log = info.event_log[-1000:]
        info.last_activity = time.time()
        return event

    def _record_event(self, session_id: str, message: BridgeMessage) -> BridgeMessage:
        """Attach a monotonically increasing sequence number and store the event."""
        info = self._sessions.get(session_id)
        if info is None:
            return message

        message.sequence_num = info.next_sequence_num
        info.next_sequence_num += 1
        info.event_log.append({
            "sequence_num": message.sequence_num,
            "type": message.type.value,
            "payload": message.payload,
            "timestamp": message.timestamp,
            "request_id": message.request_id,
        })
        if len(info.event_log) > 1000:
            info.event_log = info.event_log[-1000:]
        return message
