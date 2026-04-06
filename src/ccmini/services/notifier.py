"""Desktop / terminal notification service — port of ``services/notifier.ts``.

Auto-detects the terminal type and routes to the correct notification
method: iTerm2 escape codes, Kitty, terminal bell, macOS ``osascript``,
Linux ``notify-send``, or Windows toast.

Extended features (matching Claude Code):
- Notification hooks (modify/suppress before send)
- Preferred channel from config
- Combined channels (e.g. iTerm2 + bell)
- Notification history with duplicate suppression

Usage::

    from ccmini.services.notifier import send_notification

    await send_notification("Task complete", body="All tests passed")
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import platform
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class NotifChannel(str, Enum):
    AUTO = "auto"
    ITERM2 = "iterm2"
    ITERM2_WITH_BELL = "iterm2_with_bell"
    KITTY = "kitty"
    GHOSTTY = "ghostty"
    TERMINAL_BELL = "terminal_bell"
    OS_NATIVE = "os_native"
    DISABLED = "disabled"


# ── Terminal detection ───────────────────────────────────────────────


def _detect_terminal() -> str:
    """Return a lowercase terminal identifier from env vars."""
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    if "iterm" in term_program:
        return "iterm2"
    if "kitty" in term_program:
        return "kitty"
    if "ghostty" in term_program:
        return "ghostty"

    lc_terminal = os.environ.get("LC_TERMINAL", "").lower()
    if "iterm2" in lc_terminal:
        return "iterm2"

    if os.environ.get("KITTY_PID"):
        return "kitty"

    return "unknown"


# ── Channel implementations ──────────────────────────────────────────


def _send_bell() -> None:
    """Send terminal bell character."""
    sys.stdout.write("\a")
    sys.stdout.flush()


def _send_iterm2(title: str, body: str = "") -> None:
    """Send notification via iTerm2 proprietary escape sequence."""
    message = body if body else title
    sys.stdout.write(f"\033]9;{message}\a")
    sys.stdout.flush()


def _send_kitty(title: str, body: str = "") -> None:
    """Send notification via Kitty escape sequence."""
    payload = title
    if body:
        payload = f"{title}: {body}"
    sys.stdout.write(f"\033]99;i=1:d=0;{payload}\033\\")
    sys.stdout.flush()


def _send_ghostty(title: str, body: str = "") -> None:
    """Send notification via Ghostty (uses terminal bell + title)."""
    _send_bell()


async def _send_macos(title: str, body: str = "") -> None:
    """Send macOS native notification via osascript."""
    body_clause = f' with body "{body}"' if body else ""
    script = f'display notification "{title}"{body_clause} with title "mini-agent"'
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:
        logger.debug("macOS notification failed", exc_info=True)


async def _send_linux(title: str, body: str = "") -> None:
    """Send Linux notification via notify-send."""
    if not shutil.which("notify-send"):
        _send_bell()
        return
    args = ["notify-send", "--app-name=mini-agent", title]
    if body:
        args.append(body)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:
        logger.debug("Linux notification failed", exc_info=True)
        _send_bell()


async def _send_windows(title: str, body: str = "") -> None:
    """Send Windows toast notification via PowerShell."""
    body_xml = f"<text>{body}</text>" if body else ""
    ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
$xml.LoadXml('<toast><visual><binding template="ToastText02"><text id="1">{title}</text><text id="2">{body}</text></binding></visual></toast>')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("mini-agent").Show($toast)
"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-Command", ps_script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10.0)
    except Exception:
        logger.debug("Windows toast notification failed", exc_info=True)
        _send_bell()


# ── Notification hooks ───────────────────────────────────────────────


@dataclass
class NotificationContext:
    """Context passed to notification hooks."""
    title: str
    body: str
    channel: NotifChannel
    urgent: bool = False
    suppressed: bool = False


@runtime_checkable
class NotificationHook(Protocol):
    """Protocol for notification hooks.

    Hooks run before the notification is sent.  A hook can modify the
    context (title, body, channel) or suppress the notification entirely
    by setting ``context.suppressed = True``.
    """

    async def on_notify(
        self,
        message: str,
        channel: str,
        context: NotificationContext,
    ) -> None: ...


_notification_hooks: list[NotificationHook] = []


def register_notification_hook(hook: NotificationHook) -> None:
    """Register a hook that runs before every notification."""
    if hook not in _notification_hooks:
        _notification_hooks.append(hook)


def unregister_notification_hook(hook: NotificationHook) -> None:
    """Remove a previously registered notification hook."""
    try:
        _notification_hooks.remove(hook)
    except ValueError:
        pass


async def _execute_notification_hooks(ctx: NotificationContext) -> None:
    """Run all registered hooks. Any hook can suppress by setting ctx.suppressed."""
    for hook in _notification_hooks:
        try:
            await hook.on_notify(ctx.title, ctx.channel.value, ctx)
            if ctx.suppressed:
                break
        except Exception as exc:
            logger.debug("Notification hook error: %s", exc)


# ── Preferred channel from config ────────────────────────────────────


def get_preferred_channel() -> NotifChannel:
    """Read preferred notification channel from global config.

    Config key: ``notifications.preferred_channel``
    Options: ``"auto"``, ``"bell"``, ``"os_native"``, ``"iterm2"``,
    ``"iterm2_with_bell"``, ``"kitty"``, ``"ghostty"``,
    ``"terminal_bell"``, ``"disabled"``

    ``"auto"`` (default) detects the best available channel.
    """
    try:
        from ..config import _load_json, _global_config_path
        cfg = _load_json(_global_config_path())
    except Exception:
        return NotifChannel.AUTO

    notif_cfg = cfg.get("notifications", {})
    if isinstance(notif_cfg, dict):
        raw = notif_cfg.get("preferred_channel", "auto")
    else:
        raw = "auto"

    _alias_map: dict[str, str] = {
        "bell": "terminal_bell",
        "os": "os_native",
    }
    raw = _alias_map.get(str(raw).lower(), str(raw).lower())

    try:
        return NotifChannel(raw)
    except ValueError:
        return NotifChannel.AUTO


# ── Notification history ─────────────────────────────────────────────


@dataclass
class _HistoryEntry:
    message: str
    channel: str
    timestamp: float


class NotificationHistory:
    """Track recent notifications for dedup and inspection."""

    def __init__(self, max_entries: int = 100) -> None:
        self._entries: collections.deque[_HistoryEntry] = collections.deque(
            maxlen=max_entries,
        )

    def record(self, message: str, channel: str) -> None:
        self._entries.append(_HistoryEntry(
            message=message,
            channel=channel,
            timestamp=time.monotonic(),
        ))

    def get_recent(self, count: int = 10) -> list[dict[str, Any]]:
        """Return the last *count* notifications as dicts."""
        items = list(self._entries)[-count:]
        return [
            {"message": e.message, "channel": e.channel, "timestamp": e.timestamp}
            for e in items
        ]

    def suppress_duplicate(self, message: str, within_seconds: float = 30.0) -> bool:
        """Return True if *message* was already sent within *within_seconds*.

        Useful to avoid repeating the same notification in rapid succession.
        """
        now = time.monotonic()
        cutoff = now - within_seconds
        for entry in reversed(self._entries):
            if entry.timestamp < cutoff:
                break
            if entry.message == message:
                return True
        return False

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


_notification_history = NotificationHistory()


def get_notification_history() -> NotificationHistory:
    """Return the global notification history instance."""
    return _notification_history


# ── Public API ───────────────────────────────────────────────────────


async def send_notification(
    title: str,
    body: str = "",
    *,
    channel: NotifChannel | str = NotifChannel.AUTO,
    urgent: bool = False,
) -> None:
    """Send a notification to the user.

    Parameters
    ----------
    title:
        Short notification title.
    body:
        Optional longer body text.
    channel:
        Notification channel (default: auto-detect).  When set to AUTO
        the preferred channel from config is tried first.
    urgent:
        If True, prefer native OS notifications over terminal escape codes.
    """
    if isinstance(channel, str):
        try:
            channel = NotifChannel(channel)
        except ValueError:
            channel = NotifChannel.AUTO

    # Apply preferred channel from config when caller uses AUTO
    if channel == NotifChannel.AUTO:
        preferred = get_preferred_channel()
        if preferred != NotifChannel.AUTO:
            channel = preferred

    if channel == NotifChannel.DISABLED:
        return

    # Duplicate suppression
    display_msg = f"{title}: {body}" if body else title
    if _notification_history.suppress_duplicate(display_msg, within_seconds=10.0):
        logger.debug("Suppressing duplicate notification: %s", display_msg)
        return

    # Run hooks
    hook_ctx = NotificationContext(
        title=title, body=body, channel=channel, urgent=urgent,
    )
    await _execute_notification_hooks(hook_ctx)
    if hook_ctx.suppressed:
        return

    # Hooks may have modified title/body/channel
    title = hook_ctx.title
    body = hook_ctx.body
    channel = hook_ctx.channel

    if channel == NotifChannel.AUTO:
        if urgent:
            channel = NotifChannel.OS_NATIVE
        else:
            terminal = _detect_terminal()
            channel_map: dict[str, NotifChannel] = {
                "iterm2": NotifChannel.ITERM2,
                "kitty": NotifChannel.KITTY,
                "ghostty": NotifChannel.GHOSTTY,
            }
            channel = channel_map.get(terminal, NotifChannel.OS_NATIVE)

    if channel == NotifChannel.ITERM2:
        _send_iterm2(title, body)
    elif channel == NotifChannel.ITERM2_WITH_BELL:
        _send_iterm2(title, body)
        _send_bell()
    elif channel == NotifChannel.KITTY:
        _send_kitty(title, body)
    elif channel == NotifChannel.GHOSTTY:
        _send_ghostty(title, body)
    elif channel == NotifChannel.TERMINAL_BELL:
        _send_bell()
    elif channel == NotifChannel.OS_NATIVE:
        system = platform.system()
        if system == "Darwin":
            await _send_macos(title, body)
        elif system == "Linux":
            await _send_linux(title, body)
        elif system == "Windows":
            await _send_windows(title, body)
        else:
            _send_bell()
    else:
        _send_bell()

    # Record in history
    _notification_history.record(display_msg, channel.value)
