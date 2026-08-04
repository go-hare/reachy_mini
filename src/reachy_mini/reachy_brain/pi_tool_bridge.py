"""HTTP handlers that expose RobotIntentTools to the Pi robot-agent TS tools."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

LOGGER = logging.getLogger(__name__)


@dataclass
class PiToolBridge:
    """Route /robot/* HTTP calls onto a RobotIntentTools instance."""

    robot_tools: Any
    token: str | None = None
    current_turn_id: str = ""
    _started: bool = field(default=False, init=False, repr=False)

    def set_turn_id(self, turn_id: str | None) -> None:
        """Remember the active turn so tool calls can inherit it."""
        self.current_turn_id = str(turn_id or "").strip()

    def clear_turn_id(self) -> None:
        """Clear the active turn id after a brain turn settles."""
        self.current_turn_id = ""

    def authorized(self, headers: dict[str, str]) -> bool:
        """Return True when the optional bearer token is missing or matches."""
        if not self.token:
            return True
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip() == self.token
        return auth.strip() == self.token

    async def handle(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        query: dict[str, list[str]] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Dispatch one HTTP request. Returns (status_code, json_object)."""
        if not self.authorized(headers):
            return 401, {"error": {"code": "unauthorized", "message": "invalid token"}}

        method_u = method.upper()
        path_only = path.split("?", 1)[0]
        query = query or {}

        try:
            if method_u == "GET" and path_only in {"/health", "/robot/health"}:
                return 200, {"ok": True, "service": "pi_tool_bridge"}

            if method_u == "POST" and path_only == "/robot/emit_embodied_intent":
                payload = self._json_body(body)
                self._inject_turn_id(payload)
                result = await self.robot_tools.emit_embodied_intent(**payload)
                return 200, result

            if method_u == "GET" and path_only == "/robot/state":
                return 200, await self.robot_tools.query_robot_state()

            if method_u == "GET" and path_only == "/robot/trace":
                params = {
                    "turn_id": _first(query, "turn_id", default=""),
                    "intent_id": _first(query, "intent_id", default=""),
                    "plan_id": _first(query, "plan_id", default=""),
                    "command_id": _first(query, "command_id", default=""),
                }
                return 200, await self.robot_tools.query_robot_trace(**params)

            if method_u == "GET" and path_only == "/robot/metrics":
                return 200, await self.robot_tools.query_robot_metrics()

            if method_u == "GET" and path_only == "/robot/behavior_tree":
                return 200, await self.robot_tools.query_robot_behavior_tree()

            if method_u == "GET" and path_only == "/robot/structured_log":
                limit_raw = _first(query, "limit", default="100")
                try:
                    limit = max(1, int(limit_raw or 100))
                except ValueError:
                    limit = 100
                return 200, await self.robot_tools.query_robot_structured_log(limit=limit)

            if method_u == "POST" and path_only == "/robot/request_task":
                payload = self._json_body(body)
                self._inject_turn_id(payload)
                result = await self.robot_tools.request_robot_task(**payload)
                return 200, result

            if method_u == "POST" and path_only == "/robot/cancel_task":
                payload = self._json_body(body)
                result = await self.robot_tools.cancel_robot_task(**payload)
                return 200, result

            return 404, {
                "error": {
                    "code": "not_found",
                    "message": f"{method_u} {path_only}",
                }
            }
        except TypeError as exc:
            LOGGER.warning("pi tool bridge bad request: %s", exc)
            return 400, {
                "error": {
                    "code": "bad_request",
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            }
        except Exception as exc:  # pragma: no cover - defensive path
            LOGGER.exception("pi tool bridge handler failed")
            return 500, {
                "error": {
                    "code": "internal_error",
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            }

    def _json_body(self, body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TypeError(f"request body must be JSON object: {exc}") from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise TypeError("request body must be a JSON object")
        return data

    def _inject_turn_id(self, payload: dict[str, Any]) -> None:
        if not self.current_turn_id:
            return
        existing = payload.get("turn_id")
        if existing is None or str(existing).strip() == "":
            payload["turn_id"] = self.current_turn_id


def _first(query: dict[str, list[str]], key: str, *, default: str = "") -> str:
    values = query.get(key) or []
    if not values:
        return default
    return values[0]


def parse_query(path: str) -> dict[str, list[str]]:
    """Parse query string from a raw request path."""
    parsed = urlparse(path if "://" in path else f"http://local{path}")
    return parse_qs(parsed.query, keep_blank_values=True)


__all__ = ["PiToolBridge", "parse_query"]
