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
from reachy_mini.robot_runtime.adapters.live2d import Live2DAdapter  # noqa: E402
from reachy_mini.robot_runtime.config import RobotRuntimeConfig  # noqa: E402
from reachy_mini.robot_runtime.contracts import RuntimeMode  # noqa: E402
from reachy_mini.robot_runtime.runtime import RobotRuntime  # noqa: E402
from reachy_mini.robot_runtime.tools import RobotIntentTools  # noqa: E402
from reachy_mini.runtime.live2d_avatar import (  # noqa: E402
    Live2DCapabilities,
    Live2DNativeAction,
)


def _live2d_capabilities() -> Live2DCapabilities:
    return Live2DCapabilities(
        model_name="IceGirl",
        root_url="/static/assets/live2d/IceGirl",
        vtube_file="IceGirl.vtube.json",
        model_file="IceGirl.model3.json",
        idle_motion="DaiJi",
        motions=("DaiJi", "HuiShou"),
        expressions=(),
        motion_details=(
            Live2DNativeAction(
                name="HuiShou",
                file_name="HuiShou.motion3.json",
                aliases=("greet", "wave"),
                duration_s=2.0,
            ),
        ),
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--intent-type", default="greet")
    parser.add_argument(
        "--no-live2d",
        action="store_true",
        help="skip Live2D adapter registration (expect capability_unresolved)",
    )
    args = parser.parse_args()

    frames: list[object] = []

    async def publish_frame(frame: object) -> None:
        frames.append(frame)

    runtime = RobotRuntime(
        config=RobotRuntimeConfig(enabled=True, mode=RuntimeMode.AVATAR_ONLY)
    )
    tools = RobotIntentTools(runtime)
    server = PiToolBridgeServer(
        PiToolBridge(robot_tools=tools),
        host=args.host,
        port=args.port,
    )
    await runtime.start()
    if not args.no_live2d:
        await runtime.register_adapter(
            Live2DAdapter(_live2d_capabilities(), publish_frame=publish_frame)
        )
    await server.start()
    print(f"bridge={server.base_url}")
    try:
        # Direct tools call
        direct = await tools.emit_embodied_intent(
            intent_type=args.intent_type,
            modalities=["gesture"],
            turn_id="smoke_1",
        )
        print("direct:", json.dumps(direct, ensure_ascii=False)[:500])

        # HTTP call via stdlib
        reader, writer = await asyncio.open_connection(args.host, server._bound_port or args.port)
        body = json.dumps(
            {
                "intent_type": args.intent_type,
                "modalities": ["gesture"],
                "turn_id": "smoke_http",
            }
        ).encode()
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

        metrics = await tools.query_robot_metrics()
        print(
            "metrics:",
            {
                k: metrics.get(k)
                for k in (
                    "intent_count",
                    "adapter_success_count",
                    "capability_unresolved_count",
                )
            },
        )
        print(
            "frames:",
            [
                {
                    "action": getattr(f, "action", None),
                    "payload": getattr(f, "payload", None),
                }
                for f in frames
            ],
        )
        if args.no_live2d:
            return 0
        ok = (
            int(metrics.get("adapter_success_count") or 0) >= 1
            and int(metrics.get("capability_unresolved_count") or 0) == 0
            and any(
                getattr(f, "action", None) == "live2d_motion"
                and (getattr(f, "payload", None) or {}).get("name") == "HuiShou"
                for f in frames
            )
        )
        print("smoke:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        await server.stop()
        await runtime.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
