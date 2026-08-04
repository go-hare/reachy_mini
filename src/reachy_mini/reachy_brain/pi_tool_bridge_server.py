"""Asyncio HTTP server wrapping PiToolBridge (stdlib, no FastAPI binding)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .pi_tool_bridge import PiToolBridge, parse_query

LOGGER = logging.getLogger(__name__)


class PiToolBridgeServer:
    """Minimal HTTP/1.1 server for robot intent tools."""

    def __init__(
        self,
        bridge: PiToolBridge,
        *,
        host: str = "127.0.0.1",
        port: int = 8787,
    ) -> None:
        self.bridge = bridge
        self.host = host
        self.port = int(port)
        self._server: asyncio.AbstractServer | None = None
        self._bound_port: int | None = None

    @property
    def base_url(self) -> str:
        port = self._bound_port if self._bound_port is not None else self.port
        return f"http://{self.host}:{port}"

    @property
    def current_turn_id(self) -> str:
        return self.bridge.current_turn_id

    def set_turn_id(self, turn_id: str | None) -> None:
        self.bridge.set_turn_id(turn_id)

    def clear_turn_id(self) -> None:
        self.bridge.clear_turn_id()

    async def start(self) -> None:
        """Bind and start serving."""
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.host,
            port=self.port,
        )
        sockets = self._server.sockets or []
        if sockets:
            self._bound_port = int(sockets[0].getsockname()[1])
        else:
            self._bound_port = self.port
        LOGGER.info("pi tool bridge listening on %s", self.base_url)

    async def stop(self) -> None:
        """Stop serving and close sockets."""
        server = self._server
        self._server = None
        if server is None:
            return
        server.close()
        await server.wait_closed()
        LOGGER.info("pi tool bridge stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            parts = request_line.decode("latin-1", errors="replace").strip().split()
            if len(parts) < 2:
                await self._write_response(writer, 400, {"error": "bad request line"})
                return
            method, path = parts[0], parts[1]

            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if not line or line in {b"\r\n", b"\n"}:
                    break
                text = line.decode("latin-1", errors="replace").rstrip("\r\n")
                if ":" not in text:
                    continue
                key, value = text.split(":", 1)
                headers[key.strip().lower()] = value.strip()

            content_length = int(headers.get("content-length", "0") or 0)
            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            status, payload = await self.bridge.handle(
                method=method,
                path=path,
                headers=headers,
                body=body,
                query=parse_query(path),
            )
            await self._write_response(writer, status, payload)
        except Exception:  # pragma: no cover - connection edge cases
            LOGGER.exception("pi tool bridge connection error")
            try:
                await self._write_response(
                    writer,
                    500,
                    {"error": {"code": "connection_error", "message": "internal error"}},
                )
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _write_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        reason = {
            200: "OK",
            400: "Bad Request",
            401: "Unauthorized",
            404: "Not Found",
            500: "Internal Server Error",
        }.get(status, "OK")
        header = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("latin-1")
        writer.write(header + body)
        await writer.drain()


async def start_tool_bridge(
    robot_tools: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    token: str | None = None,
) -> PiToolBridgeServer:
    """Convenience helper used by RuntimeSession."""
    bridge = PiToolBridge(robot_tools=robot_tools, token=token)
    server = PiToolBridgeServer(bridge, host=host, port=port)
    await server.start()
    return server


__all__ = ["PiToolBridgeServer", "start_tool_bridge"]
