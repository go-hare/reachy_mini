"""Unit tests for Pi tool bridge → RobotIntentTools routing."""

from __future__ import annotations

import json
from typing import Any

import pytest

from reachy_mini.reachy_brain.pi_tool_bridge import PiToolBridge
from reachy_mini.reachy_brain.pi_tool_bridge_server import PiToolBridgeServer


class _FakeRobotTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def emit_embodied_intent(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("emit_embodied_intent", kwargs))
        return {
            "intent": {"intent_type": kwargs.get("intent_type"), "turn_id": kwargs.get("turn_id")},
            "event": {"event_type": "intent_received", "status": "ok"},
            "events": [{"event_type": "intent_received"}],
        }

    async def query_robot_state(self) -> dict[str, Any]:
        self.calls.append(("query_robot_state", {}))
        return {"revision": "1", "state": {"lifecycle": "active"}}

    async def query_robot_trace(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("query_robot_trace", kwargs))
        return {"events": [], **kwargs}

    async def query_robot_metrics(self) -> dict[str, Any]:
        self.calls.append(("query_robot_metrics", {}))
        return {"intent_count": 0}

    async def query_robot_behavior_tree(self) -> dict[str, Any]:
        self.calls.append(("query_robot_behavior_tree", {}))
        return {"root": "robot_root"}

    async def query_robot_structured_log(self, *, limit: int = 100) -> dict[str, Any]:
        self.calls.append(("query_robot_structured_log", {"limit": limit}))
        return {"line_count": 0, "lines": [], "limit": limit}

    async def request_robot_task(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("request_robot_task", kwargs))
        return {"ok": True, **kwargs}

    async def cancel_robot_task(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("cancel_robot_task", kwargs))
        return {"event_type": "task_cancelled", **kwargs}


@pytest.mark.asyncio
async def test_emit_injects_turn_id() -> None:
    tools = _FakeRobotTools()
    bridge = PiToolBridge(robot_tools=tools)
    bridge.set_turn_id("turn_abc")
    status, payload = await bridge.handle(
        method="POST",
        path="/robot/emit_embodied_intent",
        headers={"content-type": "application/json"},
        body=json.dumps({"intent_type": "greet"}).encode("utf-8"),
    )
    assert status == 200
    assert payload["intent"]["turn_id"] == "turn_abc"
    assert tools.calls[0][0] == "emit_embodied_intent"
    assert tools.calls[0][1]["intent_type"] == "greet"
    assert tools.calls[0][1]["turn_id"] == "turn_abc"


@pytest.mark.asyncio
async def test_health_and_state_routes() -> None:
    tools = _FakeRobotTools()
    bridge = PiToolBridge(robot_tools=tools)
    status, health = await bridge.handle(
        method="GET",
        path="/health",
        headers={},
        body=b"",
    )
    assert status == 200
    assert health["ok"] is True

    status, state = await bridge.handle(
        method="GET",
        path="/robot/state",
        headers={},
        body=b"",
    )
    assert status == 200
    assert state["revision"] == "1"


@pytest.mark.asyncio
async def test_token_auth() -> None:
    tools = _FakeRobotTools()
    bridge = PiToolBridge(robot_tools=tools, token="secret")
    status, payload = await bridge.handle(
        method="GET",
        path="/health",
        headers={},
        body=b"",
    )
    assert status == 401
    status, payload = await bridge.handle(
        method="GET",
        path="/health",
        headers={"authorization": "Bearer secret"},
        body=b"",
    )
    assert status == 200
    assert payload["ok"] is True


@pytest.mark.asyncio
async def test_server_http_roundtrip() -> None:
    tools = _FakeRobotTools()
    bridge = PiToolBridge(robot_tools=tools)
    server = PiToolBridgeServer(bridge, host="127.0.0.1", port=0)
    await server.start()
    try:
        # port 0 → OS-assigned
        assert server._bound_port is not None
        reader, writer = await __import__("asyncio").open_connection(
            "127.0.0.1",
            server._bound_port,
        )
        req = (
            b"GET /health HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        writer.write(req)
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert b"200" in raw.split(b"\r\n", 1)[0]
        assert b'"ok": true' in raw or b'"ok":true' in raw
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_structured_log_limit_query() -> None:
    tools = _FakeRobotTools()
    bridge = PiToolBridge(robot_tools=tools)
    status, payload = await bridge.handle(
        method="GET",
        path="/robot/structured_log?limit=12",
        headers={},
        body=b"",
        query={"limit": ["12"]},
    )
    assert status == 200
    assert tools.calls[-1] == ("query_robot_structured_log", {"limit": 12})
