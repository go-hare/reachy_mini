"""Resident runtime entry for one generated Reachy Mini app."""

from reachy_mini import ReachyMiniApp


class DemoWeb(ReachyMiniApp):
    """Host the generated app profile through the resident runtime."""

    custom_app_url: str | None = "http://0.0.0.0:8042"
    profile_root_relative_path = "profiles"


if __name__ == "__main__":
    app = DemoWeb()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()

