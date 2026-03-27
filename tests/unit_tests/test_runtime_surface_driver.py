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

    assert driver.apply_state({"thread_id": "app:test", "phase": "idle"}) == "idle"
    assert driver.current_phase == "idle"
    assert movement_manager.listening_calls[-1] is False
    assert movement_manager.activity_marks == 2


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

