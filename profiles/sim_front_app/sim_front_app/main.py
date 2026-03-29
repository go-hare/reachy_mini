"""Resident runtime entry for one generated Reachy Mini app."""

from __future__ import annotations

from pathlib import Path

from reachy_mini import ReachyMiniApp
from reachy_mini.runtime.config import load_profile_runtime_config
from reachy_mini.runtime.profile_loader import load_profile_bundle


def _log_profile_runtime_summary() -> None:
    """Print the active resident-runtime profile flags at startup."""
    profile_root = (Path(__file__).resolve().parent.parent / "profiles").resolve()
    try:
        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
    except Exception as exc:  # pragma: no cover - startup diagnostics only
        print(f"sim_front_app startup summary failed: {exc}", flush=True)
        return

    print(
        "sim_front_app runtime config: "
        f"no_camera={config.vision.no_camera} "
        f"head_tracker={config.vision.head_tracker or 'none'} "
        f"local_vision={config.vision.local_vision} "
        f"speech={config.speech.enabled} "
        f"speech_input={config.speech_input.enabled}",
        flush=True,
    )


class SimFrontApp(ReachyMiniApp):
    """Host the generated app profile through the resident runtime."""

    custom_app_url: str | None = "http://0.0.0.0:8042"
    profile_root_relative_path = "profiles"


if __name__ == "__main__":
    _log_profile_runtime_summary()
    app = SimFrontApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
