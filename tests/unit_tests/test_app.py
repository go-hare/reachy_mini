import asyncio
import threading
from pathlib import Path
from threading import Event
import time
from unittest.mock import AsyncMock

import pytest
import uvicorn
from fastapi.testclient import TestClient

from reachy_mini import ReachyMiniApp
from reachy_mini.apps import AppInfo, SourceKind
from reachy_mini.apps.manager import AppManager, AppState, AppStatus
from reachy_mini.daemon.app.main import Args, create_app
from reachy_mini.daemon.daemon import Daemon
from reachy_mini.reachy_mini import ReachyMini


@pytest.mark.asyncio
async def test_app() -> None:
    class MockApp(ReachyMiniApp):
        def run(self, reachy_mini: ReachyMini, stop_event: Event) -> None:
            time.sleep(1)  # Simulate some processing time

    args = Args(
        mockup_sim=True,
        headless=True,
        wake_up_on_start=False,
        no_media=True,
        autostart=True,
        autostart_installed_app=False,
        fastapi_port=0,
    )
    app = create_app(args)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    while not server.started:
        await asyncio.sleep(0.05)

    sockets = server.servers[0].sockets  # type: ignore[union-attr]
    port: int = sockets[0].getsockname()[1]
    daemon = app.state.daemon

    stop = Event()

    with ReachyMini(host="localhost", port=port, media_backend="no_media") as mini:
        mock_app = MockApp()
        mock_app.run(mini, stop)

    await daemon.stop(goto_sleep_on_stop=False)
    server.should_exit = True
    server_thread.join(timeout=10)


@pytest.mark.asyncio
async def test_app_manager() -> None:
    daemon = Daemon(no_media=True)
    await daemon.start(
        mockup_sim=True,
        headless=True,
        wake_up_on_start=False,
        use_audio=False,
    )

    app_mngr = AppManager()
    try:
        before_installed_apps = await app_mngr.list_available_apps(SourceKind.INSTALLED)

        app_info = AppInfo(
            name="ok_app",
            source_kind=SourceKind.LOCAL,
            extra={"path": str(Path(__file__).parent / "ok_app")},
        )
        await app_mngr.install_new_app(app_info, daemon.logger)

        after_installed_apps = await app_mngr.list_available_apps(SourceKind.INSTALLED)

        assert len(after_installed_apps) == len(before_installed_apps) + 1

        status = await app_mngr.start_app("ok_app", media_backend="no_media")
        assert status is not None and status.state in (
            AppState.STARTING,
            AppState.RUNNING,
        )
        assert app_mngr.is_app_running()
        status = await app_mngr.current_app_status()
        assert status is not None and status.state in (
            AppState.STARTING,
            AppState.RUNNING,
        )

        await app_mngr.stop_current_app()
        assert not app_mngr.is_app_running()
        status = await app_mngr.current_app_status()
        assert status is None

        await app_mngr.remove_app("ok_app", daemon.logger)
        after_uninstalled_apps = await app_mngr.list_available_apps(
            SourceKind.INSTALLED
        )

        assert len(after_uninstalled_apps) == len(before_installed_apps)

    except Exception as e:
        pytest.fail(f"install_new_app raised an exception: {e}")
    finally:
        await daemon.stop(goto_sleep_on_stop=False)


@pytest.mark.asyncio
async def test_faulty_app() -> None:
    # Start a real app server on port 8000 so the faulty app subprocess
    # can connect immediately (its _check_daemon_on_localhost checks port 8000).
    # Without a server, the subprocess falls back to reachy-mini.local DNS
    # resolution which hangs in CI, causing the test to time out.
    args = Args(
        mockup_sim=True,
        headless=True,
        wake_up_on_start=False,
        no_media=True,
        autostart=True,
        autostart_installed_app=False,
        fastapi_port=8000,
    )
    app = create_app(args)
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    while not server.started:
        await asyncio.sleep(0.05)

    daemon = app.state.daemon
    app_mngr = AppManager()

    app_info = AppInfo(
        name="faulty_app",
        source_kind=SourceKind.LOCAL,
        extra={"path": str(Path(__file__).parent / "faulty_app")},
    )
    try:
        await app_mngr.install_new_app(app_info, daemon.logger)

        status = await app_mngr.start_app("faulty_app", media_backend="no_media")

        success = False
        for _ in range(10):
            status = await app_mngr.current_app_status()
            if status is None or status.state in (AppState.STARTING, AppState.RUNNING):
                await asyncio.sleep(1.0)
                continue

            if status is not None and status.state == AppState.ERROR:
                success = True
                break

        await app_mngr.remove_app("faulty_app", daemon.logger)

        if not success:
            pytest.fail("Faulty app did not reach ERROR state in time")

    except Exception as e:
        pytest.fail(f"install_new_app raised an exception: {e}")
    finally:
        await daemon.stop(goto_sleep_on_stop=False)
        server.should_exit = True
        server_thread.join(timeout=10)


@pytest.mark.asyncio
async def test_app_manager_autostarts_single_installed_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_mngr = AppManager()

    async def fake_list_available_apps(source: SourceKind) -> list[AppInfo]:
        assert source == SourceKind.INSTALLED
        return [AppInfo(name="solo_app", source_kind=SourceKind.INSTALLED)]

    async def fake_start_app(app_name: str, *args: object, **kwargs: object) -> AppStatus:
        _ = args
        _ = kwargs
        return AppStatus(
            info=AppInfo(name=app_name, source_kind=SourceKind.INSTALLED),
            state=AppState.STARTING,
        )

    start_mock = AsyncMock(side_effect=fake_start_app)
    monkeypatch.setattr(app_mngr, "list_available_apps", fake_list_available_apps)
    monkeypatch.setattr(app_mngr, "start_app", start_mock)

    status = await app_mngr.autostart_installed_app()

    assert status is not None
    assert status.info.name == "solo_app"
    start_mock.assert_awaited_once_with("solo_app")


@pytest.mark.asyncio
async def test_app_manager_skips_autostart_when_multiple_installed_apps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_mngr = AppManager()

    async def fake_list_available_apps(source: SourceKind) -> list[AppInfo]:
        assert source == SourceKind.INSTALLED
        return [
            AppInfo(name="app_a", source_kind=SourceKind.INSTALLED),
            AppInfo(name="app_b", source_kind=SourceKind.INSTALLED),
        ]

    start_mock = AsyncMock()
    monkeypatch.setattr(app_mngr, "list_available_apps", fake_list_available_apps)
    monkeypatch.setattr(app_mngr, "start_app", start_mock)

    status = await app_mngr.autostart_installed_app()

    assert status is None
    start_mock.assert_not_awaited()


def test_create_app_lifespan_autostarts_installed_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyMdns:
        def __init__(self, robot_name: str, fastapi_port: int) -> None:
            _ = robot_name
            _ = fastapi_port

        def register(self) -> None:
            return

        def unregister(self) -> None:
            return

    args = Args(
        mockup_sim=True,
        headless=True,
        wake_up_on_start=False,
        no_media=True,
        autostart=True,
        autostart_installed_app=True,
        fastapi_port=0,
    )

    monkeypatch.setattr("reachy_mini.daemon.app.main.MdnsServiceRegistration", DummyMdns)

    app = create_app(args)
    daemon_start = AsyncMock()
    daemon_stop = AsyncMock()
    app_close = AsyncMock()
    autostart_app = AsyncMock(return_value=None)

    app.state.daemon.start = daemon_start
    app.state.daemon.stop = daemon_stop
    app.state.app_manager.close = app_close
    app.state.app_manager.autostart_installed_app = autostart_app

    with TestClient(app):
        pass

    daemon_start.assert_awaited_once()
    autostart_app.assert_awaited_once()
    app_close.assert_awaited_once()
    daemon_stop.assert_awaited_once()
