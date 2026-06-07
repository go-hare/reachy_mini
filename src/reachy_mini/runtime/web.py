"""Host-only web launcher for generated app projects."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
import uvicorn

from reachy_mini import ReachyMiniApp

from .project import DEFAULT_APP_BIND_URL, AppProject

HOST_ONLY_MINI_MAX_CALLS = 100


@dataclass(frozen=True)
class WebBinding:
    """Resolved bind and browser URLs for a hosted app."""

    bind_url: str
    host: str
    port: int
    browser_url: str


class HostedAppProject(ReachyMiniApp):
    """Small ReachyMiniApp wrapper that points at one generated app project."""

    def __init__(self, *, app_project: AppProject, bind_url: str) -> None:
        """Create a hosted app wrapper for one generated app project."""
        self._app_project = app_project
        self.custom_app_url = bind_url
        self.profile_root_relative_path = "profiles"
        super().__init__()

    def _get_instance_path(self) -> Path:
        """Reuse the generated app package paths for static and profile loading."""
        return self._app_project.main_file


class HostOnlyMini:
    """Minimal Reachy-like object for host-only web runtime smoke paths."""

    def __init__(self) -> None:
        """Create a no-hardware mini that still supports runtime helpers."""
        self.calls: list[dict[str, Any]] = []
        self._head_pose = np.eye(4, dtype=np.float64)
        self._antennas = (0.0, 0.0)
        self._body_yaw = 0.0

    def goto_target(self, **kwargs: Any) -> None:
        """Record high-level movement calls without touching hardware."""
        self._record_call("goto_target", dict(kwargs))

    def look_at_world(self, *_args: Any, **kwargs: Any) -> None:
        """Record look-at calls without touching hardware."""
        self._record_call("look_at_world", dict(kwargs))

    def set_target(
        self,
        *,
        head: Any,
        antennas: tuple[float, float],
        body_yaw: float,
    ) -> None:
        """Accept low-level movement commands from MovementManager."""
        self._head_pose = np.asarray(head, dtype=np.float64).copy()
        self._antennas = (float(antennas[0]), float(antennas[1]))
        self._body_yaw = float(body_yaw)
        self._record_call(
            "set_target",
            {
                "antennas": self._antennas,
                "body_yaw": self._body_yaw,
            },
        )

    def get_current_head_pose(self) -> Any:
        """Return the last commanded head pose."""
        return self._head_pose.copy()

    def get_current_joint_positions(self) -> tuple[float, tuple[float, float]]:
        """Return body yaw and antenna positions."""
        return self._body_yaw, self._antennas

    def get_present_antenna_joint_positions(self) -> list[float]:
        """Return antenna positions for action helpers."""
        return [self._antennas[0], self._antennas[1]]

    def _record_call(self, method: str, kwargs: dict[str, Any]) -> None:
        self.calls.append({"method": method, "kwargs": kwargs})
        del self.calls[:-HOST_ONLY_MINI_MAX_CALLS]


def resolve_web_binding(
    app_project: AppProject,
    *,
    host: str | None = None,
    port: int | None = None,
) -> WebBinding:
    """Resolve bind and browser URLs for a host-only app session."""
    parsed = urlparse(app_project.custom_app_url or DEFAULT_APP_BIND_URL)
    scheme = parsed.scheme or "http"
    bind_host = host or parsed.hostname or "127.0.0.1"
    bind_port = int(port or parsed.port or 8042)
    bind_url = f"{scheme}://{bind_host}:{bind_port}"

    browser_host = "127.0.0.1" if bind_host in {"0.0.0.0", "::"} else bind_host
    browser_url = f"{scheme}://{browser_host}:{bind_port}/"
    return WebBinding(
        bind_url=bind_url,
        host=bind_host,
        port=bind_port,
        browser_url=browser_url,
    )


def build_web_host(
    app_project: AppProject,
    *,
    bind_url: str | None = None,
) -> HostedAppProject:
    """Build a host-only ReachyMiniApp instance for one project."""
    return HostedAppProject(
        app_project=app_project,
        bind_url=bind_url or app_project.custom_app_url or DEFAULT_APP_BIND_URL,
    )


def run_web_host(
    app: ReachyMiniApp,
    *,
    host: str,
    port: int,
    startup_timeout: float = 10.0,
) -> None:
    """Run one generated app's web UI without opening a hardware connection."""
    if app.settings_app is None:
        raise RuntimeError("This app does not expose a web UI.")

    stop_event = threading.Event()
    runtime_error: dict[str, BaseException] = {}

    def _runtime_worker() -> None:
        try:
            app.run(HostOnlyMini(), stop_event)
        except BaseException as exc:  # pragma: no cover - defensive thread bridge
            runtime_error["exc"] = exc
            stop_event.set()

    worker = threading.Thread(target=_runtime_worker, daemon=True)
    worker.start()

    if not app.wait_until_runtime_ready(timeout=startup_timeout):
        stop_event.set()
        worker.join(timeout=5.0)
        if "exc" in runtime_error:
            raise RuntimeError("Resident runtime failed to start.") from runtime_error[
                "exc"
            ]
        raise TimeoutError("Timed out waiting for the resident runtime to start.")

    try:
        uvicorn.run(app.settings_app, host=host, port=port)
    finally:
        stop_event.set()
        worker.join(timeout=5.0)
