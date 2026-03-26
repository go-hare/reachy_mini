"""Speech-reactive head motion driven by assistant audio chunks."""

from __future__ import annotations

import base64
import logging
import queue
import threading
import time
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

from .speech_tapper import HOP_MS, SwayRollRT

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
MOVEMENT_LATENCY_S = 0.2
SpeechOffsets = tuple[float, float, float, float, float, float]


class HeadWobbler:
    """Convert assistant audio deltas into speech-motion offsets."""

    def __init__(self, set_speech_offsets: Callable[[SpeechOffsets], None]) -> None:
        self._apply_offsets = set_speech_offsets
        self._base_ts: float | None = None
        self._hops_done = 0

        self.audio_queue: queue.Queue[tuple[int, int, NDArray[np.int16]]] = queue.Queue()
        self.sway = SwayRollRT()

        self._state_lock = threading.Lock()
        self._sway_lock = threading.Lock()
        self._generation = 0

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def feed(self, delta_b64: str) -> None:
        """Decode one base64 PCM chunk and queue it for motion extraction."""

        buf = np.frombuffer(base64.b64decode(delta_b64), dtype=np.int16).reshape(1, -1)
        with self._state_lock:
            generation = self._generation
        self.audio_queue.put((generation, SAMPLE_RATE, buf))

    def start(self) -> None:
        """Start the consumer thread if it is not already running."""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.working_loop, daemon=True)
        self._thread.start()
        logger.debug("Head wobbler started")

    def stop(self) -> None:
        """Stop the consumer thread and clear residual offsets."""

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._apply_offsets((0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        logger.debug("Head wobbler stopped")

    def working_loop(self) -> None:
        """Convert queued audio deltas into motion offsets."""

        hop_dt = HOP_MS / 1000.0
        logger.debug("Head wobbler thread started")

        while not self._stop_event.is_set():
            queue_ref = self.audio_queue
            try:
                chunk_generation, sr, chunk = queue_ref.get_nowait()
            except queue.Empty:
                time.sleep(MOVEMENT_LATENCY_S)
                continue

            try:
                with self._state_lock:
                    current_generation = self._generation
                if chunk_generation != current_generation:
                    continue

                if self._base_ts is None:
                    with self._state_lock:
                        if self._base_ts is None:
                            self._base_ts = time.monotonic()

                pcm = np.asarray(chunk).squeeze(0)
                with self._sway_lock:
                    results = self.sway.feed(pcm, sr)

                index = 0
                while index < len(results):
                    with self._state_lock:
                        if self._generation != current_generation:
                            break
                        base_ts = self._base_ts
                        hops_done = self._hops_done

                    if base_ts is None:
                        base_ts = time.monotonic()
                        with self._state_lock:
                            if self._base_ts is None:
                                self._base_ts = base_ts
                                hops_done = self._hops_done

                    target = base_ts + MOVEMENT_LATENCY_S + hops_done * hop_dt
                    now = time.monotonic()
                    if now - target >= hop_dt:
                        lag_hops = int((now - target) / hop_dt)
                        drop = min(lag_hops, len(results) - index - 1)
                        if drop > 0:
                            with self._state_lock:
                                self._hops_done += drop
                            index += drop
                            continue

                    if target > now:
                        time.sleep(target - now)
                        with self._state_lock:
                            if self._generation != current_generation:
                                break

                    sway_frame = results[index]
                    offsets: SpeechOffsets = (
                        sway_frame["x_mm"] / 1000.0,
                        sway_frame["y_mm"] / 1000.0,
                        sway_frame["z_mm"] / 1000.0,
                        sway_frame["roll_rad"],
                        sway_frame["pitch_rad"],
                        sway_frame["yaw_rad"],
                    )

                    with self._state_lock:
                        if self._generation != current_generation:
                            break

                    self._apply_offsets(offsets)
                    with self._state_lock:
                        self._hops_done += 1
                    index += 1
            finally:
                queue_ref.task_done()

        logger.debug("Head wobbler thread exited")

    def reset(self) -> None:
        """Reset timing state, drain queued audio, and clear motion offsets."""

        with self._state_lock:
            self._generation += 1
            self._base_ts = None
            self._hops_done = 0

        while True:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
            else:
                self.audio_queue.task_done()

        with self._sway_lock:
            self.sway.reset()

        self._apply_offsets((0.0, 0.0, 0.0, 0.0, 0.0, 0.0))


__all__ = ["HeadWobbler", "MOVEMENT_LATENCY_S", "SAMPLE_RATE"]
