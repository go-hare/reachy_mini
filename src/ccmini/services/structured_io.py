"""Structured I/O — NDJSON stdin/stdout SDK communication protocol.

Ported from Claude Code's ``cli/structuredIO.ts``.

Provides a JSON-RPC style transport for embedding mini_agent into
other applications. Messages are newline-delimited JSON (NDJSON)
on stdin/stdout.

Message types (host → agent):
- ``user``          — user message
- ``control_response`` — response to a permission/hook request
- ``keep_alive``    — heartbeat
- ``update_environment_variables`` — update env vars at runtime

Message types (agent → host):
- ``assistant``     — model response text/tool calls
- ``system``        — system notifications
- ``control_request`` — permission prompt / hook callback
- ``result``        — final result of a query

Usage::

    sio = StructuredIO()
    await sio.start()

    async for msg in sio.incoming():
        if msg["type"] == "user":
            # feed to agent
            ...
        elif msg["type"] == "control_response":
            sio.resolve_control(msg["id"], msg)

    await sio.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

logger = logging.getLogger(__name__)

# ── Message types ────────────────────────────────────────────────────

INBOUND_TYPES = frozenset({
    "user",
    "control_response",
    "keep_alive",
    "update_environment_variables",
    "channel_notification",
})

OUTBOUND_TYPES = frozenset({
    "assistant",
    "system",
    "control_request",
    "result",
    "error",
})


# ── LRU set for duplicate detection ─────────────────────────────────

class _BoundedSet:
    """LRU set capped at *maxsize* entries."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._data: OrderedDict[str, None] = OrderedDict()
        self._maxsize = maxsize

    def add(self, item: str) -> None:
        if item in self._data:
            self._data.move_to_end(item)
        else:
            self._data[item] = None
            if len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def __contains__(self, item: str) -> bool:
        return item in self._data


# ── StructuredIO ─────────────────────────────────────────────────────


@dataclass
class ControlRequest:
    """A pending control request awaiting host response."""
    id: str
    type: str
    payload: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]
    created_at: float = field(default_factory=time.monotonic)


class StructuredIO:
    """NDJSON stdin/stdout transport for SDK integration."""

    def __init__(
        self,
        *,
        reader: asyncio.StreamReader | None = None,
        writer: asyncio.StreamWriter | None = None,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._running = False
        self._pending: dict[str, ControlRequest] = {}
        self._resolved_ids = _BoundedSet(1000)
        self._message_counter = 0
        self._on_control_request: Callable[[dict[str, Any]], None] | None = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start reading from stdin."""
        if self._running:
            return
        self._running = True

        if self._reader is None:
            loop = asyncio.get_event_loop()
            self._reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(self._reader)
            await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        asyncio.ensure_future(self._read_loop())
        logger.debug("StructuredIO started")

    async def stop(self) -> None:
        """Stop reading and cancel pending requests."""
        self._running = False
        for req in self._pending.values():
            if not req.future.done():
                req.future.cancel()
        self._pending.clear()

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Incoming ─────────────────────────────────────────────────

    async def incoming(self) -> AsyncIterator[dict[str, Any]]:
        """Yield incoming messages (user, keep_alive, env updates)."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield msg
            except asyncio.TimeoutError:
                continue

    async def _read_loop(self) -> None:
        """Background reader for stdin NDJSON lines."""
        assert self._reader is not None
        while self._running:
            try:
                line = await self._reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON on stdin: %.200s", text)
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "control_response":
                    self._handle_control_response(msg)
                elif msg_type == "keep_alive":
                    pass  # discard silently
                elif msg_type == "update_environment_variables":
                    self._handle_env_update(msg)
                elif msg_type in INBOUND_TYPES:
                    await self._queue.put(msg)
                else:
                    logger.debug("Unknown inbound message type: %s", msg_type)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("StructuredIO read error: %s", exc)
                await asyncio.sleep(0.1)

    def _handle_control_response(self, msg: dict[str, Any]) -> None:
        """Resolve a pending control request."""
        msg_id = msg.get("id", "")
        if not msg_id:
            return

        if msg_id in self._resolved_ids:
            logger.debug("Duplicate control_response ignored: %s", msg_id)
            return

        req = self._pending.pop(msg_id, None)
        if req is None:
            logger.debug("No pending request for control_response: %s", msg_id)
            return

        self._resolved_ids.add(msg_id)
        if not req.future.done():
            req.future.set_result(msg)

    def _handle_env_update(self, msg: dict[str, Any]) -> None:
        """Update process environment variables."""
        env_vars = msg.get("env", {})
        for key, value in env_vars.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        logger.debug("Updated %d environment variables", len(env_vars))

    # ── Outgoing ─────────────────────────────────────────────────

    def send(self, msg: dict[str, Any]) -> None:
        """Write an NDJSON message to stdout."""
        try:
            line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception as exc:
            logger.error("StructuredIO write error: %s", exc)

    def send_assistant(self, content: str, **extra: Any) -> None:
        """Send an assistant message."""
        self.send({"type": "assistant", "content": content, **extra})

    def send_system(self, message: str, **extra: Any) -> None:
        """Send a system notification."""
        self.send({"type": "system", "message": message, **extra})

    def send_result(self, result: Any, **extra: Any) -> None:
        """Send a final result."""
        self.send({"type": "result", "result": result, **extra})

    def send_error(self, error: str, **extra: Any) -> None:
        """Send an error."""
        self.send({"type": "error", "error": error, **extra})

    # ── Control requests ─────────────────────────────────────────

    async def request_permission(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Send a ``can_use_tool`` control request and wait for response.

        Returns the host's response dict (with ``"allow"`` bool).
        """
        return await self._send_control_request(
            "can_use_tool",
            {"tool_name": tool_name, "tool_input": tool_input},
            timeout=timeout,
        )

    async def request_hook_callback(
        self,
        hook_name: str,
        payload: dict[str, Any],
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Send a hook callback request and wait for response."""
        return await self._send_control_request(
            "hook_callback",
            {"hook_name": hook_name, **payload},
            timeout=timeout,
        )

    async def _send_control_request(
        self,
        request_type: str,
        payload: dict[str, Any],
        *,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Send a control request and await response."""
        self._message_counter += 1
        msg_id = f"ctrl_{self._message_counter}_{int(time.time() * 1000)}"

        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        req = ControlRequest(
            id=msg_id,
            type=request_type,
            payload=payload,
            future=future,
        )
        self._pending[msg_id] = req

        self.send({
            "type": "control_request",
            "id": msg_id,
            "request_type": request_type,
            **payload,
        })

        if self._on_control_request:
            self._on_control_request({"id": msg_id, "type": request_type, **payload})

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            return {"id": msg_id, "error": "timeout", "allow": False}

    # ── Hooks ────────────────────────────────────────────────────

    def set_on_control_request(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        """Set a callback invoked when a control request is sent (for bridge support)."""
        self._on_control_request = callback

    def inject_control_response(self, response: dict[str, Any]) -> None:
        """Inject a control response programmatically (for bridge support)."""
        self._handle_control_response(response)

    # ── Stats ────────────────────────────────────────────────────

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def message_count(self) -> int:
        return self._message_counter
