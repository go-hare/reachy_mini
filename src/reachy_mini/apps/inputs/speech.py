"""Speech input events for the resident app host."""

from __future__ import annotations

import base64
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field


class UserSpeechStartedEvent(BaseModel):
    """One browser speech-start lifecycle event over WebSocket."""

    type: Literal["user_speech_started"]
    thread_id: str = "app:main"
    session_id: str | None = None
    user_id: str = "user"
    text: str = ""


class UserSpeechPartialEvent(BaseModel):
    """One browser partial speech-transcript event over WebSocket."""

    type: Literal["user_speech_partial"]
    thread_id: str = "app:main"
    session_id: str | None = None
    user_id: str = "user"
    text: str = Field(min_length=1)


class UserSpeechStoppedEvent(BaseModel):
    """One browser speech-stop lifecycle event over WebSocket."""

    type: Literal["user_speech_stopped"]
    thread_id: str = "app:main"
    session_id: str | None = None
    user_id: str = "user"
    text: str = ""


class BrowserAudioChunkEvent(BaseModel):
    """One browser microphone PCM16 chunk sent over WebSocket."""

    type: Literal["browser_audio_chunk"]
    audio_b64: str = Field(min_length=1)
    sample_rate_hz: int = Field(gt=0)
    thread_id: str = "app:main"
    session_id: str | None = None
    user_id: str = "user"


class BrowserAudioStopEvent(BaseModel):
    """One browser microphone stop marker over WebSocket."""

    type: Literal["browser_audio_stop"]
    thread_id: str = "app:main"
    session_id: str | None = None
    user_id: str = "user"


BrowserSpeechEvent = UserSpeechStartedEvent | UserSpeechPartialEvent | UserSpeechStoppedEvent
BrowserAudioEvent = BrowserAudioChunkEvent | BrowserAudioStopEvent


def decode_browser_audio_chunk(audio_b64: str) -> np.ndarray | None:
    """Decode one base64 PCM16 payload from the browser into mono samples."""

    payload = str(audio_b64 or "").strip()
    if not payload:
        return None

    try:
        pcm16 = base64.b64decode(payload)
    except Exception:
        return None

    if not pcm16 or len(pcm16) % 2 != 0:
        return None
    return np.frombuffer(pcm16, dtype=np.int16).copy()


__all__ = [
    "BrowserAudioChunkEvent",
    "BrowserAudioEvent",
    "BrowserAudioStopEvent",
    "BrowserSpeechEvent",
    "decode_browser_audio_chunk",
    "UserSpeechPartialEvent",
    "UserSpeechStartedEvent",
    "UserSpeechStoppedEvent",
]
