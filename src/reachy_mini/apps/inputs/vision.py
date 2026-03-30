"""Vision input events for the resident app host."""

from __future__ import annotations

import base64
from typing import Any, Literal

from pydantic import BaseModel, Field


class BrowserCameraFrameEvent(BaseModel):
    """One browser-camera frame sent over WebSocket for runtime-side perception."""

    type: Literal["browser_camera_frame"]
    image_b64: str = Field(min_length=1)
    thread_id: str = "app:main"


def ingest_browser_camera_frame(
    event: BrowserCameraFrameEvent,
    runtime_tool_context: Any | None,
) -> bool:
    """Decode one browser frame and inject it into the camera worker when available."""

    camera_worker = (
        getattr(runtime_tool_context, "camera_worker", None)
        if runtime_tool_context is not None
        else None
    )
    if camera_worker is None or not hasattr(camera_worker, "ingest_external_frame"):
        return False

    frame = decode_browser_camera_frame(event.image_b64)
    if frame is None:
        return False
    camera_worker.ingest_external_frame(frame)
    return True


def decode_browser_camera_frame(image_b64: str) -> Any | None:
    """Decode one JPEG base64 payload from the browser into a BGR frame."""

    payload = str(image_b64 or "").strip()
    if not payload:
        return None
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]

    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    try:
        raw = base64.b64decode(payload)
    except Exception:
        return None

    buffer = np.frombuffer(raw, dtype=np.uint8)
    if buffer.size == 0:
        return None
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


__all__ = [
    "BrowserCameraFrameEvent",
    "decode_browser_camera_frame",
    "ingest_browser_camera_frame",
]
