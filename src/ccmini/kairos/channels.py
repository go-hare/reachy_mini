"""Channel system — MCP channel notification intake and gating.

Port of Claude Code's services/mcp/channelNotification.ts and the
KAIROS_CHANNELS feature. MCP servers declaring the `claude/channel`
capability can push inbound messages (from Slack, GitHub, etc.) into
the agent's conversation. The agent sees them wrapped in <channel> tags
and decides which tool to reply with.

Architecture:
  MCP Server (with channel capability)
    -> push notification
    -> ChannelGate (auth + policy + allowlist)
    -> wrap in <channel> tag
    -> enqueue in CommandQueue
    -> SleepTool polls queue, wakes within 1s
    -> Agent processes message
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from ..paths import mini_agent_path
from .core import feature, get_kairos_state, get_gate_config
from .sleep import CommandQueue, QueuedCommand, get_command_queue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Channel entry types
# ---------------------------------------------------------------------------

class ChannelKind(str, Enum):
    PLUGIN = "plugin"     # marketplace plugin (verified)
    SERVER = "server"     # raw MCP server (needs dev flag)


@dataclass
class ChannelEntry:
    """A registered channel endpoint."""
    kind: ChannelKind
    name: str
    server_name: str = ""          # MCP server that owns this channel
    marketplace_id: str = ""       # for plugin kind
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def tag(self) -> str:
        return f"{self.kind.value}:{self.name}"


# ---------------------------------------------------------------------------
# Channel notification
# ---------------------------------------------------------------------------

@dataclass
class ChannelNotification:
    """An inbound message from a channel."""
    channel: ChannelEntry
    content: str
    sender: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    def to_tagged_content(self) -> str:
        """Wrap the notification in a <channel> tag for the model."""
        attrs = f' source="{self.channel.tag}"'
        if self.sender:
            attrs += f' sender="{self.sender}"'
        return f"<channel{attrs}>\n{self.content}\n</channel>"


# ---------------------------------------------------------------------------
# Channel gate (mirrors channelNotification.ts gateChannelServer)
# ---------------------------------------------------------------------------

class GateResult(str, Enum):
    ALLOWED = "allowed"
    FEATURE_DISABLED = "feature_disabled"
    RUNTIME_DISABLED = "runtime_disabled"
    NOT_IN_ALLOWLIST = "not_in_allowlist"
    SERVER_NOT_ALLOWED = "server_not_allowed"


@dataclass
class ChannelGateContext:
    """Context for channel gating decisions."""
    channels_enabled: bool = False
    allowed_plugins: list[str] = field(default_factory=list)
    dev_mode: bool = False


def gate_channel(
    channel: ChannelEntry,
    ctx: ChannelGateContext | None = None,
) -> GateResult:
    """Gate an MCP server's channel notification.

    Order: feature flag -> runtime config -> allowlist.
    """
    if not (feature("kairos") or feature("kairos_channels")):
        return GateResult.FEATURE_DISABLED

    if ctx is None:
        cfg = get_gate_config()
        state = get_kairos_state()
        ctx = ChannelGateContext(
            channels_enabled=cfg.channels_enabled,
            allowed_plugins=state.allowed_channel_plugins,
        )

    if not ctx.channels_enabled:
        return GateResult.RUNTIME_DISABLED

    if channel.kind == ChannelKind.PLUGIN:
        if ctx.allowed_plugins and channel.name not in ctx.allowed_plugins:
            return GateResult.NOT_IN_ALLOWLIST
        return GateResult.ALLOWED

    if channel.kind == ChannelKind.SERVER:
        return GateResult.ALLOWED

    return GateResult.NOT_IN_ALLOWLIST


# ---------------------------------------------------------------------------
# Channel registry
# ---------------------------------------------------------------------------

class ChannelRegistry:
    """Manages registered channels and dispatches notifications."""

    def __init__(self) -> None:
        self._channels: dict[str, ChannelEntry] = {}
        self._handlers: dict[str, list[Any]] = {}  # channel_tag -> handlers
        self._notification_log: list[ChannelNotification] = []

    def register(self, channel: ChannelEntry) -> None:
        self._channels[channel.tag] = channel
        logger.debug("Channel registered: %s", channel.tag)

    def unregister(self, tag: str) -> None:
        self._channels.pop(tag, None)
        self._handlers.pop(tag, None)

    def get(self, tag: str) -> ChannelEntry | None:
        return self._channels.get(tag)

    def list_channels(self) -> list[ChannelEntry]:
        return list(self._channels.values())

    async def handle_notification(
        self,
        notification: ChannelNotification,
        *,
        gate_ctx: ChannelGateContext | None = None,
    ) -> bool:
        """Process an inbound channel notification.

        1. Gate check
        2. Wrap in <channel> tag
        3. Enqueue in command queue for the agent
        Returns True if the notification was accepted.
        """
        result = gate_channel(notification.channel, gate_ctx)
        if result != GateResult.ALLOWED:
            logger.debug(
                "Channel notification rejected: %s (%s)",
                notification.channel.tag,
                result.value,
            )
            return False

        self._notification_log.append(notification)
        try:
            get_channel_persistence().save_unread(
                ChannelMessage.from_notification(notification)
            )
        except Exception:
            logger.debug("Failed to persist unread channel message", exc_info=True)

        tagged_content = notification.to_tagged_content()
        cmd = QueuedCommand(
            source="channel",
            content=tagged_content,
            priority="next",
            metadata={
                "channel": notification.channel.tag,
                "sender": notification.sender,
                "raw_content": notification.content,
            },
        )

        queue = get_command_queue()
        await queue.enqueue(cmd)
        logger.debug(
            "Channel notification enqueued from %s",
            notification.channel.tag,
        )
        return True

    def get_notification_log(self, *, limit: int = 100) -> list[ChannelNotification]:
        return self._notification_log[-limit:]

    def clear_log(self) -> None:
        self._notification_log.clear()


_registry: ChannelRegistry | None = None


def get_channel_registry() -> ChannelRegistry:
    global _registry
    if _registry is None:
        _registry = ChannelRegistry()
    return _registry


# ---------------------------------------------------------------------------
# MCP capability detection
# ---------------------------------------------------------------------------

def has_channel_capability(server_capabilities: dict[str, Any]) -> bool:
    """Check if an MCP server declares the claude/channel capability."""
    experimental = server_capabilities.get("experimental", {})
    return "claude/channel" in experimental or "channel" in experimental


def register_channel_from_mcp(
    server_name: str,
    server_capabilities: dict[str, Any],
    *,
    kind: ChannelKind = ChannelKind.SERVER,
) -> ChannelEntry | None:
    """Auto-register a channel from an MCP server's capabilities."""
    if not has_channel_capability(server_capabilities):
        return None

    channel_config = (
        server_capabilities.get("experimental", {}).get("claude/channel")
        or server_capabilities.get("experimental", {}).get("channel")
        or {}
    )

    channel = ChannelEntry(
        kind=kind,
        name=channel_config.get("name", server_name),
        server_name=server_name,
        marketplace_id=channel_config.get("marketplace_id", ""),
        metadata=channel_config,
    )

    registry = get_channel_registry()
    registry.register(channel)
    return channel


def channel_notification_from_mcp_payload(
    server_name: str,
    payload: Any,
) -> ChannelNotification | None:
    """Coerce MCP notification payloads into channel notifications.

    Supports structured logging payloads shaped like:
    - ``{"type": "channel_notification", "content": "...", "channel": "server:x"}``
    - ``{"type": "channel", "content": "...", "sender": "...", "metadata": {...}}``
    """
    if isinstance(payload, str):
        try:
            import json

            payload = json.loads(payload)
        except Exception:
            return None

    if not isinstance(payload, dict):
        return None

    payload_type = str(payload.get("type", "")).lower()
    if payload_type not in {"channel", "channel_notification"}:
        return None

    content = str(payload.get("content", "")).strip()
    if not content:
        return None

    channel_name = str(payload.get("channel", "")).strip()
    if ":" in channel_name:
        channel_tag = channel_name
        channel = get_channel_registry().get(channel_tag)
        if channel is None:
            kind_text, _, bare_name = channel_tag.partition(":")
            kind = ChannelKind.PLUGIN if kind_text == ChannelKind.PLUGIN.value else ChannelKind.SERVER
            channel = ChannelEntry(kind=kind, name=bare_name or server_name, server_name=server_name)
            get_channel_registry().register(channel)
    else:
        channel = ChannelEntry(
            kind=ChannelKind.SERVER,
            name=channel_name or server_name,
            server_name=server_name,
        )
        get_channel_registry().register(channel)

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return ChannelNotification(
        channel=channel,
        content=content,
        sender=str(payload.get("sender", "")),
        metadata=metadata,
        raw=payload,
    )


# ---------------------------------------------------------------------------
# System prompt section
# ---------------------------------------------------------------------------

def get_channels_system_prompt() -> str | None:
    """Return channel-related system prompt section."""
    registry = get_channel_registry()
    channels = registry.list_channels()
    if not channels:
        return None

    lines = [
        "# Channels",
        "",
        "You have access to the following communication channels. When a "
        "message arrives from a channel, it will be wrapped in a <channel> "
        "tag with the source and sender attributes.",
        "",
    ]
    for ch in channels:
        lines.append(f"- **{ch.name}** ({ch.kind.value}): server={ch.server_name}")

    lines.extend([
        "",
        "When you receive a channel message, decide how to respond:",
        "- Use the channel's MCP tool to reply in-channel",
        "- Use SendUserMessage to notify the user",
        "- Or both, depending on context",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Message serialization
# ---------------------------------------------------------------------------

import json
from pathlib import Path

_CHANNEL_MSG_SCHEMA_KEYS = frozenset({
    "channel_tag", "content", "sender", "timestamp", "metadata", "priority",
})


@dataclass
class ChannelMessage:
    """Wire-format message for serialization and persistence."""
    channel_tag: str
    content: str
    sender: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_tag": self.channel_tag,
            "content": self.content,
            "sender": self.sender,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChannelMessage:
        return cls(
            channel_tag=data["channel_tag"],
            content=data["content"],
            sender=data.get("sender", ""),
            timestamp=data.get("timestamp", time.time()),
            metadata=data.get("metadata", {}),
            priority=data.get("priority", "normal"),
        )

    @classmethod
    def from_notification(cls, notif: ChannelNotification) -> ChannelMessage:
        return cls(
            channel_tag=notif.channel.tag,
            content=notif.content,
            sender=notif.sender,
            timestamp=notif.timestamp,
            metadata=notif.metadata,
            priority="normal",
        )


def _validate_channel_message_dict(data: dict[str, Any]) -> list[str]:
    """Validate that *data* has the required ChannelMessage schema keys."""
    errors: list[str] = []
    for key in ("channel_tag", "content"):
        if key not in data:
            errors.append(f"missing required key: {key}")
    unknown = set(data.keys()) - _CHANNEL_MSG_SCHEMA_KEYS
    if unknown:
        errors.append(f"unknown keys: {sorted(unknown)}")
    return errors


def serialize_channel_message(msg: ChannelMessage) -> str:
    """Serialize a ChannelMessage to a JSON string."""
    return json.dumps(msg.to_dict(), ensure_ascii=False)


def deserialize_channel_message(json_str: str) -> ChannelMessage:
    """Deserialize a JSON string to a ChannelMessage.

    Raises ``ValueError`` on schema validation failure.
    """
    data = json.loads(json_str)
    errors = _validate_channel_message_dict(data)
    if errors:
        raise ValueError(f"Invalid channel message: {'; '.join(errors)}")
    return ChannelMessage.from_dict(data)


# ---------------------------------------------------------------------------
# Channel persistence — save unread messages to disk
# ---------------------------------------------------------------------------

_CHANNELS_DIR = mini_agent_path("channels")


class ChannelPersistence:
    """Persist unread channel messages to ``~/.mini_agent/channels/``.

    Each channel gets its own JSON file containing an ordered list of
    unread messages. Messages are appended on receive and removed once
    the agent has processed them.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._dir = Path(base_dir) if base_dir else _CHANNELS_DIR

    def _channel_path(self, channel_tag: str) -> Path:
        safe_name = re.sub(r"[^\w\-.]", "_", channel_tag)
        return self._dir / f"{safe_name}.json"

    def _load_messages(self, channel_tag: str) -> list[dict[str, Any]]:
        path = self._channel_path(channel_tag)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("messages", [])
        except (json.JSONDecodeError, OSError):
            return []

    def _save_messages(self, channel_tag: str, messages: list[dict[str, Any]]) -> None:
        path = self._channel_path(channel_tag)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "channel_tag": channel_tag,
            "updated_at": time.time(),
            "messages": messages,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def save_unread(self, msg: ChannelMessage) -> None:
        """Append an unread message to the channel's persistent store."""
        messages = self._load_messages(msg.channel_tag)
        messages.append(msg.to_dict())
        self._save_messages(msg.channel_tag, messages)

    def load_unread(self, channel_tag: str) -> list[ChannelMessage]:
        """Load all unread messages for a channel."""
        raw = self._load_messages(channel_tag)
        result: list[ChannelMessage] = []
        for entry in raw:
            try:
                result.append(ChannelMessage.from_dict(entry))
            except (KeyError, TypeError):
                logger.warning("Skipping corrupt channel message in %s", channel_tag)
        return result

    def load_all_unread(self) -> dict[str, list[ChannelMessage]]:
        """Load unread messages for all channels."""
        result: dict[str, list[ChannelMessage]] = {}
        if not self._dir.exists():
            return result
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                tag = data.get("channel_tag", path.stem)
                msgs = [ChannelMessage.from_dict(m) for m in data.get("messages", [])]
                if msgs:
                    result[tag] = msgs
            except (json.JSONDecodeError, OSError, KeyError):
                continue
        return result

    def list_channels_with_unread(self) -> list[str]:
        """Return channel tags that currently have unread persisted messages."""
        return sorted(self.load_all_unread().keys())

    def mark_read(self, channel_tag: str, before_ts: float | None = None) -> int:
        """Remove messages from persistent store. Returns count removed.

        If *before_ts* is given, only messages with timestamp <= before_ts
        are removed. Otherwise all messages are cleared.
        """
        messages = self._load_messages(channel_tag)
        if not messages:
            return 0
        if before_ts is None:
            count = len(messages)
            self._save_messages(channel_tag, [])
            return count
        kept = [m for m in messages if m.get("timestamp", 0) > before_ts]
        removed = len(messages) - len(kept)
        self._save_messages(channel_tag, kept)
        return removed

    def clear_all(self) -> None:
        """Remove all persisted channel messages."""
        if not self._dir.exists():
            return
        for path in self._dir.glob("*.json"):
            try:
                path.unlink()
            except OSError:
                pass


_channel_persistence: ChannelPersistence | None = None


def get_channel_persistence() -> ChannelPersistence:
    global _channel_persistence
    if _channel_persistence is None:
        _channel_persistence = ChannelPersistence()
    return _channel_persistence


# ---------------------------------------------------------------------------
# Channel filtering — subscribe with sender/type/priority filters
# ---------------------------------------------------------------------------

FilterCallback = Callable[[ChannelMessage], Awaitable[None]]


@dataclass
class ChannelFilter:
    """Filter criteria for channel subscriptions."""
    sender: str | None = None
    message_type: str | None = None
    priority: str | None = None

    def matches(self, msg: ChannelMessage) -> bool:
        if self.sender is not None and msg.sender != self.sender:
            return False
        if self.message_type is not None:
            msg_type = msg.metadata.get("type", "")
            if msg_type != self.message_type:
                return False
        if self.priority is not None and msg.priority != self.priority:
            return False
        return True


@dataclass
class FilteredSubscription:
    channel_tag: str
    filter: ChannelFilter
    callback: FilterCallback


class FilteredChannelDispatcher:
    """Routes channel messages to filtered subscribers."""

    def __init__(self) -> None:
        self._subscriptions: list[FilteredSubscription] = []

    def subscribe_with_filter(
        self,
        channel_tag: str,
        channel_filter: ChannelFilter,
        callback: FilterCallback,
    ) -> None:
        self._subscriptions.append(
            FilteredSubscription(channel_tag, channel_filter, callback)
        )

    def unsubscribe_all(self, channel_tag: str | None = None) -> int:
        """Remove subscriptions. Returns count removed."""
        if channel_tag is None:
            count = len(self._subscriptions)
            self._subscriptions.clear()
            return count
        before = len(self._subscriptions)
        self._subscriptions = [
            s for s in self._subscriptions if s.channel_tag != channel_tag
        ]
        return before - len(self._subscriptions)

    async def route(self, msg: ChannelMessage) -> int:
        """Route a message to matching subscribers. Returns match count."""
        matched = 0
        for sub in self._subscriptions:
            if sub.channel_tag != "*" and sub.channel_tag != msg.channel_tag:
                continue
            if sub.filter.matches(msg):
                try:
                    await sub.callback(msg)
                    matched += 1
                except Exception:
                    logger.exception(
                        "Filter callback error for %s", sub.channel_tag,
                    )
        return matched

    @property
    def subscription_count(self) -> int:
        return len(self._subscriptions)


_filter_router: FilteredChannelDispatcher | None = None


def get_channel_filter_router() -> FilteredChannelDispatcher:
    global _filter_router
    if _filter_router is None:
        _filter_router = FilteredChannelDispatcher()
    return _filter_router
