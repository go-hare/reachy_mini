"""Tests for the runtime surface driver."""

from reachy_mini.runtime.surface_driver import SurfaceDriver


class FakeMovementManager:
    """Collect surface-driver side effects without starting motion threads."""

    def __init__(self) -> None:
        self.listening_calls: list[bool] = []
        self.activity_marks = 0

    def set_listening(self, listening: bool) -> None:
        self.listening_calls.append(bool(listening))

    def mark_activity(self) -> None:
        self.activity_marks += 1


def test_surface_driver_maps_phases_to_listening_and_activity() -> None:
    """Lifecycle phases should translate into MovementManager baseline signals."""
    movement_manager = FakeMovementManager()
    driver = SurfaceDriver(movement_manager=movement_manager)

    assert driver.apply_state({"thread_id": "app:test", "phase": "listening"}) == "listening"
    assert movement_manager.listening_calls == [True]
    assert movement_manager.activity_marks == 1

    assert driver.apply_state({"thread_id": "app:test", "phase": "replying"}) == "replying"
    assert movement_manager.listening_calls[-1] is False
    assert movement_manager.activity_marks == 2

    assert (
        driver.apply_state({"thread_id": "app:test", "phase": "listening_wait"})
        == "listening_wait"
    )
    assert movement_manager.listening_calls[-1] is False
    assert movement_manager.activity_marks == 3

    assert driver.apply_state({"thread_id": "app:test", "phase": "idle"}) == "idle"
    assert driver.current_phase == "idle"
    assert movement_manager.listening_calls[-1] is False
    assert movement_manager.activity_marks == 3


def test_surface_driver_aggregates_concurrent_thread_phases() -> None:
    """One thread going idle should not silence another active thread."""
    movement_manager = FakeMovementManager()
    driver = SurfaceDriver(movement_manager=movement_manager)

    assert driver.apply_state({"thread_id": "thread-a", "phase": "replying"}) == "replying"
    assert driver.apply_state({"thread_id": "thread-b", "phase": "listening"}) == "listening"

    assert driver.apply_state({"thread_id": "thread-b", "phase": "idle"}) == "replying"
    assert driver.current_phase == "replying"

    assert driver.apply_state({"thread_id": "thread-a", "phase": "idle"}) == "idle"
    assert driver.current_phase == "idle"


def test_surface_driver_exposes_richer_aggregate_state() -> None:
    """The aggregate driver state should preserve richer surface semantics, not just phase."""
    driver = SurfaceDriver()

    driver.apply_state(
        {
            "thread_id": "thread-a",
            "phase": "replying",
            "presence": "near",
            "body_state": "leaning_in",
        }
    )
    driver.apply_state(
        {
            "thread_id": "thread-b",
            "phase": "listening",
            "presence": "beside",
            "body_state": "listening_beside",
        }
    )

    assert driver.current_state["phase"] == "listening"
    assert driver.current_state["presence"] == "beside"
    assert driver.current_state["body_state"] == "listening_beside"

    driver.apply_state({"thread_id": "thread-b", "phase": "idle"})

    assert driver.current_state["phase"] == "replying"
    assert driver.current_state["presence"] == "near"
    assert driver.current_state["body_state"] == "leaning_in"


def test_surface_driver_holds_settling_until_recommended_hold_expires() -> None:
    """A short settling hold should survive an immediate idle handoff."""
    fake_time = {"value": 10.0}
    driver = SurfaceDriver(now_fn=lambda: fake_time["value"])

    assert (
        driver.apply_state(
            {
                "thread_id": "thread-a",
                "phase": "settling",
                "presence": "steady",
                "recommended_hold_ms": 900,
            }
        )
        == "settling"
    )
    assert driver.apply_state({"thread_id": "thread-a", "phase": "idle"}) == "settling"
    assert driver.current_state["phase"] == "settling"
    assert driver.current_state["presence"] == "steady"

    fake_time["value"] = 10.95

    assert driver.current_phase == "idle"
    assert driver.current_state == {"phase": "idle"}

