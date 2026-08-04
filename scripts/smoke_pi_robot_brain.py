#!/usr/bin/env python3
"""Soft smoke: start RobotRuntime + Pi tool bridge and emit one intent."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from monorepo without install.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reachy_mini.reachy_brain.pi_tool_bridge import PiToolBridge  # noqa: E402
from reachy_mini.reachy_brain.pi_tool_bridge_server import PiToolBridgeServer  # noqa: E402
from reachy_mini.robot_runtime.config import RobotRuntimeConfig  # noqa: E402
from reachy_mini.robot_runtime.runtime import RobotRuntime  # noqa: E402
from reachy_mini.robot_runtime.tools import RobotIntentTools  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--intent-type", default="greet")
    args = parser.parse_args()

    runtime = RobotRuntime(config=RobotRuntimeConfig(enabled=True))
    tools = RobotIntentTools(runtime)
    server = PiToolBridgeServer(
        PiToolBridge(robot_tools=tools),
        host=args.host,
        port=args.port,
    )
    await runtime.start()
    await server.start()
    print(f"bridge={server.base_url}")
    try:
        # Direct tools call
        direct = await tools.emit_embodied_intent(intent_type=args.intent_type, turn_id="smoke_1")
        print("direct:", json.dumps(direct, ensure_ascii=False)[:500])

        # HTTP call via stdlib
        reader, writer = await asyncio.open_connection(args.host, server._bound_port or args.port)
        body = json.dumps({"intent_type": args.intent_type, "turn_id": "smoke_http"}).encode()
        req = (
            b"POST /robot/emit_embodied_intent HTTP/1.1\r\n"
            + f"Host: {args.host}\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        writer.write(req)
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        print("http:", raw.decode("utf-8", errors="replace")[:800])

        # health
        reader, writer = await asyncio.open_connection(args.host, server._bound_port or args.port)
        writer.write(b"GET /health HTTP/1.1\r\nHost: local\r\nConnection: close\r\n\r\n")
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        print("health:", raw.decode("utf-8", errors="replace")[:300])
        return 0
    finally:
        await server.stop()
        await runtime.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
