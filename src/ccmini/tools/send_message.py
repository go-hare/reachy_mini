"""SendMessageTool for background agents and swarm teammates."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..delegation.background import BackgroundAgentRunner
from ..delegation.mailbox import FileMailbox, MailboxMessage
from ..delegation.team_files import (
    TEAM_LEAD_NAME,
    get_leader_team_name,
    get_team_mailbox_dir,
    read_team_file,
    sanitize_name,
)
from ..bridge import RemoteBridgeSession, RemoteBridgeSessionConfig
from ..tool import Tool, ToolUseContext
from .list_peers import list_live_peers, send_session_message

logger = logging.getLogger(__name__)

DESCRIPTION = "Send a message to another agent."

INSTRUCTIONS = """\
Send a follow-up message to either:

- a background agent task (resume / continue work), or
- a teammate in the current swarm team mailbox.

Use `to="*"` for team broadcast.
Structured messages are supported for shutdown and plan approval flows.
"""


class MessageType(str, Enum):
    TEXT = "text"
    REQUEST = "request"
    RESPONSE = "response"
    BROADCAST = "broadcast"
    STATUS = "status"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_REQUEST = "plan_approval_request"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"


@dataclass
class StructuredMessage:
    type: MessageType
    payload: str
    correlation_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)

    def encode(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "type": self.type.value,
                "payload": self.payload,
                "correlation_id": self.correlation_id,
                "metadata": self.metadata,
                "timestamp": self.timestamp,
            },
            ensure_ascii=False,
        )


class SendMessageTool(Tool):
    name = "SendMessage"
    description = DESCRIPTION
    instructions = INSTRUCTIONS
    is_read_only = False

    def __init__(self, runner: BackgroundAgentRunner, *, team_create_tool: Any | None = None) -> None:
        self._runner = runner
        self._team_create_tool = team_create_tool
        self._history: deque[dict[str, Any]] = deque(maxlen=200)

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient: teammate name, '*', 'uds:<socket-path>', or 'bridge:<session-id>'.",
                },
                "summary": {
                    "type": "string",
                    "description": "Optional short summary shown as a preview in the UI.",
                },
                "message": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "shutdown_request",
                                        "shutdown_response",
                                        "plan_approval_response",
                                    ],
                                },
                                "request_id": {"type": "string"},
                                "approve": {"type": "boolean"},
                                "reason": {"type": "string"},
                                "feedback": {"type": "string"},
                            },
                            "required": ["type"],
                        },
                    ],
                    "description": "Plain text or a structured swarm control message.",
                },
            },
            "required": ["to", "message"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        recipient = str(
            kwargs.get("to")
            or kwargs.get("task_id")
            or kwargs.get("agentId")
            or ""
        ).strip()
        raw_message = kwargs.get("message")
        broadcast = bool(kwargs.get("broadcast", False))
        summary = str(kwargs.get("summary", "")).strip() or None
        message_type_str = str(kwargs.get("message_type", "text")).strip() or "text"
        correlation_id = str(kwargs.get("correlation_id", "")).strip()

        if not recipient and not broadcast:
            return "Error: 'to' is required."
        if raw_message in (None, ""):
            return "Error: 'message' is required."
        if "@" in recipient:
            return "Error: 'to' must be a bare teammate name, task id, or '*'."
        local_peers = {peer.session_id for peer in list_live_peers(cleanup_stale=False)}
        if isinstance(raw_message, str):
            is_cross_session = (
                recipient.startswith("bridge:")
                or recipient.startswith("uds:")
                or recipient in local_peers
            )
            if recipient != "" and not is_cross_session and not summary:
                return "Error: 'summary' is required when message is a string."

        normalized_struct: dict[str, Any] | None = None
        if isinstance(raw_message, dict):
            normalized_struct = self._normalize_structured_message(raw_message, sender_name="")
            msg_type = str(normalized_struct.get("type", "")).strip()
            if recipient == "*" or broadcast:
                return "Error: structured messages cannot be broadcast."
            if msg_type == "shutdown_response" and recipient != TEAM_LEAD_NAME:
                return f'Error: shutdown response must be sent to "{TEAM_LEAD_NAME}".'

        sender_name = self._sender_name_from_context(context)
        if normalized_struct is not None:
            normalized_struct = self._normalize_structured_message(raw_message, sender_name=sender_name)
        structured_payload = self._encode_message_payload(raw_message, sender_name=sender_name, normalized=normalized_struct)

        if recipient.startswith("bridge:"):
            if not isinstance(raw_message, str):
                return "Error: structured messages are not supported for bridge recipients."
            return await self._send_to_bridge(recipient, raw_message, summary=summary)
        if recipient.startswith("uds:"):
            if not isinstance(raw_message, str):
                return "Error: structured messages are not supported for uds recipients."
            return await self._send_to_uds(recipient, raw_message, summary=summary)

        try:
            msg_type = MessageType(message_type_str)
        except ValueError:
            msg_type = MessageType.TEXT

        structured = StructuredMessage(
            type=msg_type,
            payload=structured_payload,
            correlation_id=correlation_id,
            metadata={"turn_id": context.turn_id, "agent_id": context.agent_id},
        )

        if not broadcast and recipient:
            accepted = self._runner.send_message(recipient, structured_payload if isinstance(raw_message, str) else structured_payload)
            if accepted:
                self._record_history(recipient, structured)
                resolved_id = self._runner.resolve_task_ref(recipient) or recipient.strip()
                task = self._runner.get_status(resolved_id)
                status = task.status.value if task is not None and hasattr(task.status, "value") else "unknown"
                return f"Message sent to task '{recipient}' (status: {status})."

        if recipient in local_peers and isinstance(raw_message, dict):
            return "Error: structured messages cannot be sent cross-session — only plain text."
        if recipient in local_peers:
            send_session_message(
                recipient,
                from_name=sender_name,
                text=structured_payload,
                summary=summary,
            )
            self._record_history(recipient, structured)
            return f"Message sent to local session '{recipient}'."

        if broadcast or recipient == "*":
            return await self._broadcast_to_team(
                structured_payload,
                summary=summary,
                sender_name=sender_name,
                context=context,
            )

        return await self._send_to_team_recipient(
            recipient,
            structured_payload,
            summary=summary,
            raw_message=raw_message,
            normalized_message=normalized_struct,
            sender_name=sender_name,
            context=context,
        )

    def _normalize_structured_message(self, raw_message: dict[str, Any], *, sender_name: str) -> dict[str, Any]:
        msg_type = str(raw_message.get("type", "")).strip()
        if msg_type == "shutdown_request":
            payload = {
                "type": "shutdown_request",
                "request_id": str(raw_message.get("request_id", "")).strip() or uuid.uuid4().hex[:12],
                "from": sender_name,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            reason = str(raw_message.get("reason", "")).strip()
            if reason:
                payload["reason"] = reason
            return payload
        if msg_type == "shutdown_response":
            approved = bool(raw_message.get("approve", False))
            payload = {
                "type": "shutdown_response",
                "request_id": str(raw_message.get("request_id", "")).strip(),
                "approve": approved,
                "from": sender_name,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            reason = str(raw_message.get("reason", "")).strip()
            if reason:
                payload["reason"] = reason
            return payload
        if msg_type == "plan_approval_request":
            payload = {
                "type": "plan_approval_request",
                "request_id": str(raw_message.get("request_id", "")).strip() or uuid.uuid4().hex[:12],
                "from": sender_name,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "planContent": str(raw_message.get("planContent", "")).strip(),
                "planFilePath": str(raw_message.get("planFilePath", "")).strip(),
            }
            return payload
        if msg_type == "plan_approval_response":
            payload = {
                "type": "plan_approval_response",
                "request_id": str(raw_message.get("request_id", "")).strip(),
                "approve": bool(raw_message.get("approve", False)),
                "from": sender_name,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            permission_mode = str(raw_message.get("permissionMode", "")).strip()
            if permission_mode:
                payload["permissionMode"] = permission_mode
            feedback = str(raw_message.get("feedback", "")).strip()
            if feedback:
                payload["feedback"] = feedback
            return payload
        return dict(raw_message)

    def _encode_message_payload(self, raw_message: Any, *, sender_name: str, normalized: dict[str, Any] | None = None) -> str:
        if isinstance(raw_message, str):
            return raw_message

        if not isinstance(raw_message, dict):
            return str(raw_message)
        payload = normalized if normalized is not None else self._normalize_structured_message(raw_message, sender_name=sender_name)
        return json.dumps(payload, ensure_ascii=False)

    def _active_team_name(self, context: ToolUseContext | None = None) -> str:
        if context is not None:
            agent_id = str(getattr(context, "agent_id", "") or "").strip()
            if "@" in agent_id:
                team_name = sanitize_name(agent_id.split("@", 1)[1].strip())
                if read_team_file(team_name) is not None:
                    return team_name
        team_name = get_leader_team_name()
        if team_name:
            team_name = sanitize_name(team_name)
            if read_team_file(team_name) is not None:
                return team_name
        if self._team_create_tool is not None:
            team_name = sanitize_name(str(getattr(self._team_create_tool, "_active_team_name", "")).strip())
            if team_name and read_team_file(team_name) is not None:
                return team_name
        return ""

    def _sender_name_from_context(self, context: ToolUseContext) -> str:
        agent_id = str(getattr(context, "agent_id", "") or "").strip()
        if "@" in agent_id:
            name = agent_id.split("@", 1)[0].strip()
            if name:
                return name
        extras = getattr(context, "extras", {}) or {}
        for key in ("agent_name", "sender_name"):
            value = str(extras.get(key, "")).strip()
            if value:
                return value
        return TEAM_LEAD_NAME

    async def _send_to_bridge(self, recipient: str, message: str, *, summary: str | None) -> str:
        session_id = recipient.split(":", 1)[1].strip()
        if not session_id:
            return "Error: bridge recipient must be bridge:<session-id>."

        base_url = os.environ.get("CCMINI_BRIDGE_BASE_URL", "").strip()
        auth_token = os.environ.get("CCMINI_BRIDGE_AUTH_TOKEN", "").strip()
        if not base_url or not auth_token:
            return (
                "Error: bridge messaging requires CCMINI_BRIDGE_BASE_URL and "
                "CCMINI_BRIDGE_AUTH_TOKEN in the environment."
            )

        session = RemoteBridgeSession(
            RemoteBridgeSessionConfig(
                base_url=base_url,
                auth_token=auth_token,
                session_id=session_id,
                mode="polling",
            )
        )
        try:
            await session.start()
            await session.query(message)
            return f"Message sent to bridge session '{session_id}'." + (f" Summary: {summary}" if summary else "")
        except Exception as exc:
            return f"Error sending to bridge session '{session_id}': {exc}"
        finally:
            try:
                await session.stop()
            except Exception:
                pass

    async def _send_to_uds(self, recipient: str, message: str, *, summary: str | None) -> str:
        import asyncio

        socket_path = recipient.split(":", 1)[1].strip()
        if not socket_path:
            return "Error: uds recipient must be uds:<socket-path>."

        writer = None
        try:
            _reader, writer = await asyncio.open_unix_connection(socket_path)
            writer.write(message.encode("utf-8"))
            await writer.drain()
            return f"Message sent to uds socket '{socket_path}'." + (f" Summary: {summary}" if summary else "")
        except Exception as exc:
            return f"Error sending to uds socket '{socket_path}': {exc}"
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    async def _send_to_team_recipient(
        self,
        recipient: str,
        payload: str,
        *,
        summary: str | None,
        raw_message: Any,
        normalized_message: dict[str, Any] | None,
        sender_name: str,
        context: ToolUseContext,
    ) -> str:
        team_name = self._active_team_name(context)
        if not team_name:
            return f"No task found with ID '{recipient}', and no active team mailbox is available."

        team_file = read_team_file(team_name)
        if team_file is None:
            return f"Active team '{team_name}' is missing its team file."

        members = team_file.get("members", [])
        target_name = None
        target_active = True
        for member in members if isinstance(members, list) else []:
            if not isinstance(member, dict):
                continue
            name = str(member.get("name", "")).strip()
            agent_id = str(member.get("agentId", "")).strip()
            if recipient in {name, agent_id}:
                target_name = name
                target_active = member.get("isActive", True) is not False
                break
        if target_name is None:
            return f"No task or teammate found with recipient '{recipient}'."
        if not target_active:
            return (
                f"Error: teammate '{target_name}' is inactive (isActive=false). "
                "Restore or replace the worker before sending messages."
            )

        mailbox = FileMailbox(get_team_mailbox_dir(team_name))
        mailbox.send(
            target_name,
            MailboxMessage(
                from_agent=sender_name,
                text=payload,
                summary=summary,
                msg_type="shutdown" if isinstance(raw_message, dict) and raw_message.get("type") == "shutdown_request" else "message",
            ),
        )
        history_type = (
            MessageType(str(raw_message.get("type")).strip())
            if isinstance(raw_message, dict) and str(raw_message.get("type", "")).strip() in {item.value for item in MessageType}
            else MessageType.TEXT
        )
        self._record_history(target_name, StructuredMessage(type=history_type, payload=payload))
        if normalized_message is not None:
            msg_type = str(normalized_message.get("type", "")).strip()
            request_id = str(normalized_message.get("request_id", "")).strip()
            if msg_type == "shutdown_request":
                return f"Shutdown request sent to {target_name}. Request ID: {request_id or '(generated)'}"
            if msg_type == "shutdown_response":
                if bool(normalized_message.get("approve", False)):
                    return f"Shutdown approved for request {request_id or '(unknown)'}."
                reason = str(normalized_message.get("reason", "")).strip() or "Shutdown rejected."
                return f'Shutdown rejected for request {request_id or "(unknown)"}. Reason: "{reason}"'
            if msg_type == "plan_approval_request":
                return f"Plan approval request sent to {target_name}. Request ID: {request_id or '(generated)'}"
            if msg_type == "plan_approval_response":
                approved = bool(normalized_message.get("approve", False))
                if approved:
                    return f"Plan approved for {target_name}. Request ID: {request_id or '(unknown)'}"
                feedback = str(normalized_message.get("feedback", "")).strip() or "Plan rejected."
                return f'Plan rejected for {target_name}. Request ID: {request_id or "(unknown)"}. Feedback: "{feedback}"'
        return f"Message sent to teammate '{target_name}' in team '{team_name}'."

    async def _broadcast_to_team(
        self,
        payload: str,
        *,
        summary: str | None,
        sender_name: str,
        context: ToolUseContext,
    ) -> str:
        team_name = self._active_team_name(context)
        if not team_name:
            return "Error: team broadcast requires an active team."

        team_file = read_team_file(team_name)
        if team_file is None:
            return f"Active team '{team_name}' is missing its team file."

        members = team_file.get("members", [])
        recipients = []
        for member in members if isinstance(members, list) else []:
            if not isinstance(member, dict):
                continue
            name = str(member.get("name", "")).strip()
            if not name or name == TEAM_LEAD_NAME:
                continue
            if member.get("isActive", True) is False:
                continue
            recipients.append(name)

        if not recipients:
            return "No teammates to broadcast to."

        mailbox = FileMailbox(get_team_mailbox_dir(team_name))
        for target_name in recipients:
            mailbox.send(
                target_name,
                MailboxMessage(
                    from_agent=sender_name,
                    text=payload,
                    summary=summary,
                ),
            )
            self._record_history(target_name, StructuredMessage(type=MessageType.BROADCAST, payload=payload))
        return f"Broadcast sent to {len(recipients)} teammate(s): {', '.join(recipients)}"

    def list_available_peers(self) -> list[str]:
        active = self._runner.list_active()
        return [t.id for t in active]

    def _record_history(self, target: str, msg: StructuredMessage) -> None:
        self._history.append(
            {
                "timestamp": time.time(),
                "target": target,
                "type": msg.type.value,
                "payload_preview": msg.payload[:120],
                "correlation_id": msg.correlation_id or None,
            }
        )

    def get_sent_history(self, count: int = 20) -> list[dict[str, Any]]:
        items = list(self._history)
        return items[-count:]
