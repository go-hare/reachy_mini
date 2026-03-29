"""Tests for the lightweight reactive-vision event emitter beside CameraWorker."""

from __future__ import annotations

import numpy as np

from reachy_mini.runtime.camera_worker import CameraWorker, ReactiveVisionEvent


class FakeMedia:
    """Tiny media stub for camera-worker tests."""

    def get_frame(self):
        return np.zeros((64, 64, 3), dtype=np.uint8)


class FakeRobot:
    """Tiny robot stub exposing only the media API CameraWorker needs."""

    def __init__(self) -> None:
        self.media = FakeMedia()


class FakeTracker:
    """Tracker stub that returns one scripted observation per call."""

    def __init__(self, observations: list[tuple[object, object, object]]) -> None:
        self._observations = list(observations)

    def get_head_observation(self, frame):
        _ = frame
        if not self._observations:
            return None, None, None
        return self._observations.pop(0)


def test_camera_worker_emits_detect_and_attention_events_on_first_target() -> None:
    """The first acquired target should emit discrete person + attention events."""
    tracker = FakeTracker(
        [
            (
                np.array([-0.8, 0.0], dtype=np.float32),
                0.0,
                0.91,
            )
        ]
    )
    worker = CameraWorker(FakeRobot(), head_tracker=tracker)
    events: list[ReactiveVisionEvent] = []
    worker.add_reactive_vision_listener(events.append)

    worker._process_frame(
        frame=np.zeros((64, 64, 3), dtype=np.uint8),
        current_time=10.0,
        neutral_pose=np.eye(4, dtype=np.float64),
    )

    assert [event.name for event in events] == [
        "person_detected",
        "attention_acquired",
    ]
    assert events[0].metadata["target_id"] == "primary"
    assert events[0].metadata["direction"] == "left"
    assert events[0].metadata["confidence"] == 0.91
    assert events[1].metadata["tracking_enabled"] is True
    assert worker.get_face_tracking_offsets()[5] != 0.0


def test_camera_worker_emits_loss_and_release_once_after_face_lost_delay() -> None:
    """Face loss should emit one loss/release pair only after the configured delay."""
    tracker = FakeTracker(
        [
            (
                np.array([0.0, 0.0], dtype=np.float32),
                0.0,
                0.82,
            ),
            (None, None, None),
            (None, None, None),
        ]
    )
    worker = CameraWorker(FakeRobot(), head_tracker=tracker)
    events: list[ReactiveVisionEvent] = []
    worker.add_reactive_vision_listener(events.append)

    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    neutral_pose = np.eye(4, dtype=np.float64)

    worker._process_frame(frame=frame, current_time=10.0, neutral_pose=neutral_pose)
    worker._process_frame(frame=frame, current_time=11.0, neutral_pose=neutral_pose)
    assert [event.name for event in events] == [
        "person_detected",
        "attention_acquired",
    ]

    worker._process_frame(frame=frame, current_time=12.2, neutral_pose=neutral_pose)
    worker._process_frame(frame=frame, current_time=13.5, neutral_pose=neutral_pose)

    assert [event.name for event in events] == [
        "person_detected",
        "attention_acquired",
        "person_lost",
        "attention_released",
    ]
    assert events[2].metadata["return_to_center"] is True
    assert events[3].metadata["reason"] == "lost"


def test_camera_worker_emits_disabled_attention_release_without_person_lost() -> None:
    """Disabling tracking should release attention immediately without a fake loss event."""
    tracker = FakeTracker(
        [
            (
                np.array([0.0, -0.8], dtype=np.float32),
                0.0,
                0.77,
            )
        ]
    )
    worker = CameraWorker(FakeRobot(), head_tracker=tracker)
    events: list[ReactiveVisionEvent] = []
    worker.add_reactive_vision_listener(events.append)

    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    neutral_pose = np.eye(4, dtype=np.float64)

    worker._process_frame(frame=frame, current_time=5.0, neutral_pose=neutral_pose)
    worker.set_head_tracking_enabled(False)
    worker._process_frame(frame=frame, current_time=5.1, neutral_pose=neutral_pose)

    assert [event.name for event in events] == [
        "person_detected",
        "attention_acquired",
        "attention_released",
    ]
    assert events[-1].metadata["reason"] == "disabled"
    assert events[-1].metadata["return_to_center"] is False
