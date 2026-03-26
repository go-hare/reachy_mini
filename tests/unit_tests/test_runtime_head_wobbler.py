"""Tests for the migrated audio-reactive head wobbler."""

import base64
import time
from unittest.mock import patch

import numpy as np

from reachy_mini.runtime.audio.head_wobbler import HeadWobbler


class FakeSway:
    """Small sway stub that returns a deterministic single frame."""

    def __init__(self) -> None:
        self.reset_called = False

    def feed(self, pcm: np.ndarray, sr: int) -> list[dict[str, float]]:
        _ = pcm, sr
        return [
            {
                "x_mm": 4.0,
                "y_mm": -2.0,
                "z_mm": 1.0,
                "roll_rad": 0.1,
                "pitch_rad": -0.2,
                "yaw_rad": 0.3,
            }
        ]

    def reset(self) -> None:
        self.reset_called = True


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_head_wobbler_feeds_speech_offsets() -> None:
    """Queued audio deltas should become speech offsets."""

    captured_offsets: list[tuple[float, float, float, float, float, float]] = []
    wobbler = HeadWobbler(captured_offsets.append)
    wobbler.sway = FakeSway()

    pcm = np.array([0, 1, -1, 2], dtype=np.int16)
    encoded = base64.b64encode(pcm.tobytes()).decode("utf-8")

    with patch("reachy_mini.runtime.audio.head_wobbler.MOVEMENT_LATENCY_S", 0.0):
        wobbler.start()
        try:
            wobbler.feed(encoded)
            assert _wait_for(lambda: len(captured_offsets) >= 1)
        finally:
            wobbler.stop()

    assert captured_offsets[0] == (0.004, -0.002, 0.001, 0.1, -0.2, 0.3)
    assert captured_offsets[-1] == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_head_wobbler_reset_drains_queue_and_clears_offsets() -> None:
    """reset should clear queued audio and zero out speech offsets."""

    captured_offsets: list[tuple[float, float, float, float, float, float]] = []
    wobbler = HeadWobbler(captured_offsets.append)
    fake_sway = FakeSway()
    wobbler.sway = fake_sway

    pcm = np.array([1, 2, 3, 4], dtype=np.int16)
    encoded = base64.b64encode(pcm.tobytes()).decode("utf-8")
    wobbler.feed(encoded)
    wobbler.feed(encoded)

    wobbler.reset()

    assert wobbler.audio_queue.empty()
    assert fake_sway.reset_called is True
    assert captured_offsets[-1] == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
