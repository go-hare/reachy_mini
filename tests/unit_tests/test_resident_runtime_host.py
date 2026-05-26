"""Phase 2 resident runtime host acceptance tests."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from reachy_mini.apps.app import ReachyMiniApp
from reachy_mini.pipeline.session import RuntimeSession
from reachy_mini.reachy_brain.offline_sdk_client import OfflineSDKClient
from reachy_mini.runtime.project import create_app_project


class _ResidentRuntimeProbeApp(ReachyMiniApp):
    """Generated-app host that injects an explicit SDK fake for tests."""

    def __init__(self, *, profile_root: Path, app_main: Path) -> None:
        self.profile_root_relative_path = str(profile_root)
        self._app_main = app_main
        super().__init__()

    def _get_instance_path(self) -> Path:
        return self._app_main

    def build_runtime(self, profile_root: Path) -> RuntimeSession:
        return RuntimeSession.from_profile(
            profile_root,
            client_factory=lambda options: OfflineSDKClient(options),
        )


def test_resident_runtime_host_starts_and_stops_runtime_session(tmp_path: Path) -> None:
    """Resident host owns a v4 RuntimeSession without any scheduler shim."""
    app_root = create_app_project(tmp_path / "resident_probe", "resident_probe")
    profile_root = app_root / "profiles"
    app = _ResidentRuntimeProbeApp(
        profile_root=profile_root,
        app_main=app_root / "resident_probe" / "main.py",
    )
    stop_event = threading.Event()
    worker = threading.Thread(
        target=app.run,
        args=(SimpleNamespace(), stop_event),
        daemon=True,
    )
    worker.start()

    try:
        assert app.wait_until_runtime_ready(timeout=5.0)
        assert isinstance(app.runtime, RuntimeSession)
        assert app.runtime.agent._client_factory is not None
    finally:
        stop_event.set()
        worker.join(timeout=5.0)

    assert not worker.is_alive()
    assert app.runtime is None
