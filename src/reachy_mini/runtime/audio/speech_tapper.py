"""Audio sway extraction used for speech-reactive head motion."""

from __future__ import annotations

import math
from collections import deque
from itertools import islice
from typing import Any

import numpy as np
from numpy.typing import NDArray

SR = 16_000
FRAME_MS = 20
HOP_MS = 50

SWAY_MASTER = 1.5
SENS_DB_OFFSET = +4.0
VAD_DB_ON = -35.0
VAD_DB_OFF = -45.0
VAD_ATTACK_MS = 40
VAD_RELEASE_MS = 250
ENV_FOLLOW_GAIN = 0.65

SWAY_F_PITCH = 2.2
SWAY_A_PITCH_DEG = 4.5
SWAY_F_YAW = 0.6
SWAY_A_YAW_DEG = 7.5
SWAY_F_ROLL = 1.3
SWAY_A_ROLL_DEG = 2.25
SWAY_F_X = 0.35
SWAY_A_X_MM = 4.5
SWAY_F_Y = 0.45
SWAY_A_Y_MM = 3.75
SWAY_F_Z = 0.25
SWAY_A_Z_MM = 2.25

SWAY_DB_LOW = -46.0
SWAY_DB_HIGH = -18.0
LOUDNESS_GAMMA = 0.9
SWAY_ATTACK_MS = 50
SWAY_RELEASE_MS = 250

FRAME = int(SR * FRAME_MS / 1000)
HOP = int(SR * HOP_MS / 1000)
ATTACK_FR = max(1, int(VAD_ATTACK_MS / HOP_MS))
RELEASE_FR = max(1, int(VAD_RELEASE_MS / HOP_MS))
SWAY_ATTACK_FR = max(1, int(SWAY_ATTACK_MS / HOP_MS))
SWAY_RELEASE_FR = max(1, int(SWAY_RELEASE_MS / HOP_MS))


def _rms_dbfs(x: NDArray[np.float32]) -> float:
    """Compute RMS loudness in dBFS for mono float32 audio in [-1, 1]."""

    samples = x.astype(np.float32, copy=False)
    rms = np.sqrt(np.mean(samples * samples, dtype=np.float32) + 1e-12, dtype=np.float32)
    return float(20.0 * math.log10(float(rms) + 1e-12))


def _loudness_gain(db: float, offset: float = SENS_DB_OFFSET) -> float:
    """Normalize dB into [0, 1] with gamma shaping."""

    value = (db + offset - SWAY_DB_LOW) / (SWAY_DB_HIGH - SWAY_DB_LOW)
    if value < 0.0:
        value = 0.0
    elif value > 1.0:
        value = 1.0
    return value**LOUDNESS_GAMMA if LOUDNESS_GAMMA != 1.0 else value


def _to_float32_mono(x: NDArray[Any]) -> NDArray[np.float32]:
    """Convert arbitrary PCM input to mono float32 in [-1, 1]."""

    samples = np.asarray(x)
    if samples.ndim == 0:
        return np.zeros(0, dtype=np.float32)

    if samples.ndim == 2:
        if samples.shape[0] <= 8 and samples.shape[0] <= samples.shape[1]:
            samples = np.mean(samples, axis=0)
        else:
            samples = np.mean(samples, axis=1)
    elif samples.ndim > 2:
        samples = np.mean(samples.reshape(samples.shape[0], -1), axis=0)

    if np.issubdtype(samples.dtype, np.floating):
        return samples.astype(np.float32, copy=False)

    info = np.iinfo(samples.dtype)
    scale = float(max(-info.min, info.max))
    return samples.astype(np.float32) / (scale if scale != 0.0 else 1.0)


def _resample_linear(
    x: NDArray[np.float32],
    sr_in: int,
    sr_out: int,
) -> NDArray[np.float32]:
    """Use lightweight linear resampling for short audio buffers."""

    if sr_in == sr_out or x.size == 0:
        return x

    n_out = int(round(x.size * sr_out / sr_in))
    if n_out <= 1:
        return np.zeros(0, dtype=np.float32)

    t_in = np.linspace(0.0, 1.0, num=x.size, dtype=np.float32, endpoint=True)
    t_out = np.linspace(0.0, 1.0, num=n_out, dtype=np.float32, endpoint=True)
    return np.interp(t_out, t_in, x).astype(np.float32, copy=False)


class SwayRollRT:
    """Incrementally convert audio chunks into per-hop motion offsets."""

    def __init__(self, rng_seed: int = 7) -> None:
        self._seed = int(rng_seed)
        self.samples: deque[float] = deque(maxlen=10 * SR)
        self.carry: NDArray[np.float32] = np.zeros(0, dtype=np.float32)

        self.vad_on = False
        self.vad_above = 0
        self.vad_below = 0

        self.sway_env = 0.0
        self.sway_up = 0
        self.sway_down = 0

        rng = np.random.default_rng(self._seed)
        self.phase_pitch = float(rng.random() * 2 * math.pi)
        self.phase_yaw = float(rng.random() * 2 * math.pi)
        self.phase_roll = float(rng.random() * 2 * math.pi)
        self.phase_x = float(rng.random() * 2 * math.pi)
        self.phase_y = float(rng.random() * 2 * math.pi)
        self.phase_z = float(rng.random() * 2 * math.pi)
        self.t = 0.0

    def reset(self) -> None:
        """Reset VAD, envelope, and buffered audio state."""

        self.samples.clear()
        self.carry = np.zeros(0, dtype=np.float32)
        self.vad_on = False
        self.vad_above = 0
        self.vad_below = 0
        self.sway_env = 0.0
        self.sway_up = 0
        self.sway_down = 0
        self.t = 0.0

    def feed(self, pcm: NDArray[Any], sr: int | None) -> list[dict[str, float]]:
        """Process one PCM chunk and return zero or more sway frames."""

        sr_in = SR if sr is None else int(sr)
        samples = _to_float32_mono(pcm)
        if samples.size == 0:
            return []
        if sr_in != SR:
            samples = _resample_linear(samples, sr_in, SR)
            if samples.size == 0:
                return []

        if self.carry.size:
            self.carry = np.concatenate([self.carry, samples])
        else:
            self.carry = samples

        output: list[dict[str, float]] = []

        while self.carry.size >= HOP:
            hop = self.carry[:HOP]
            self.carry = self.carry[HOP:]

            self.samples.extend(hop.tolist())
            if len(self.samples) < FRAME:
                self.t += HOP_MS / 1000.0
                continue

            frame = np.fromiter(
                islice(self.samples, len(self.samples) - FRAME, len(self.samples)),
                dtype=np.float32,
                count=FRAME,
            )
            db = _rms_dbfs(frame)

            if db >= VAD_DB_ON:
                self.vad_above += 1
                self.vad_below = 0
                if not self.vad_on and self.vad_above >= ATTACK_FR:
                    self.vad_on = True
            elif db <= VAD_DB_OFF:
                self.vad_below += 1
                self.vad_above = 0
                if self.vad_on and self.vad_below >= RELEASE_FR:
                    self.vad_on = False

            if self.vad_on:
                self.sway_up = min(SWAY_ATTACK_FR, self.sway_up + 1)
                self.sway_down = 0
            else:
                self.sway_down = min(SWAY_RELEASE_FR, self.sway_down + 1)
                self.sway_up = 0

            up = self.sway_up / SWAY_ATTACK_FR
            down = 1.0 - (self.sway_down / SWAY_RELEASE_FR)
            target = up if self.vad_on else down
            self.sway_env += ENV_FOLLOW_GAIN * (target - self.sway_env)
            if self.sway_env < 0.0:
                self.sway_env = 0.0
            elif self.sway_env > 1.0:
                self.sway_env = 1.0

            loud = _loudness_gain(db) * SWAY_MASTER
            env = self.sway_env
            self.t += HOP_MS / 1000.0

            pitch = (
                math.radians(SWAY_A_PITCH_DEG)
                * loud
                * env
                * math.sin(2 * math.pi * SWAY_F_PITCH * self.t + self.phase_pitch)
            )
            yaw = (
                math.radians(SWAY_A_YAW_DEG)
                * loud
                * env
                * math.sin(2 * math.pi * SWAY_F_YAW * self.t + self.phase_yaw)
            )
            roll = (
                math.radians(SWAY_A_ROLL_DEG)
                * loud
                * env
                * math.sin(2 * math.pi * SWAY_F_ROLL * self.t + self.phase_roll)
            )
            x_mm = (
                SWAY_A_X_MM
                * loud
                * env
                * math.sin(2 * math.pi * SWAY_F_X * self.t + self.phase_x)
            )
            y_mm = (
                SWAY_A_Y_MM
                * loud
                * env
                * math.sin(2 * math.pi * SWAY_F_Y * self.t + self.phase_y)
            )
            z_mm = (
                SWAY_A_Z_MM
                * loud
                * env
                * math.sin(2 * math.pi * SWAY_F_Z * self.t + self.phase_z)
            )

            output.append(
                {
                    "pitch_rad": pitch,
                    "yaw_rad": yaw,
                    "roll_rad": roll,
                    "pitch_deg": math.degrees(pitch),
                    "yaw_deg": math.degrees(yaw),
                    "roll_deg": math.degrees(roll),
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "z_mm": z_mm,
                }
            )

        return output


__all__ = ["HOP_MS", "SwayRollRT"]
