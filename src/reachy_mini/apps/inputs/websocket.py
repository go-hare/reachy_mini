"""WebSocket payload routing for app-side input events."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import ValidationError

from .runtime_dispatch import dispatch_user_speech_event, dispatch_user_turn
from .speech import (
    BrowserAudioChunkEvent,
    BrowserAudioStopEvent,
    UserSpeechPartialEvent,
    UserSpeechStartedEvent,
    UserSpeechStoppedEvent,
)
from .text import UserTextEvent
from .vision import BrowserCameraFrameEvent, ingest_browser_camera_frame


async def handle_runtime_websocket_payload(
    app: Any,
    *,
    payload: dict[str, Any],
    outbound_queue: asyncio.Queue[dict[str, Any]],
    tracked_thread_ids: set[str],
) -> None:
    """Parse one browser payload and dispatch it into the matching input lane."""

    event_type = str(payload.get("type", "") or "").strip()

    if event_type == "ping":
        await outbound_queue.put({"type": "pong"})
        return

    if event_type == "user_text":
        try:
            event = UserTextEvent.model_validate(payload)
        except ValidationError as exc:
            await outbound_queue.put(
                app._build_turn_error_event(
                    thread_id=str(payload.get("thread_id", "app:main") or "app:main"),
                    error=str(exc),
                )
            )
            return

        tracked_thread_ids.add(str(event.thread_id or "app:main"))
        asyncio.create_task(dispatch_user_turn(app, event, outbound_queue))
        return

    if event_type in {
        "user_speech_started",
        "user_speech_partial",
        "user_speech_stopped",
    }:
        try:
            if event_type == "user_speech_started":
                speech_event = UserSpeechStartedEvent.model_validate(payload)
            elif event_type == "user_speech_partial":
                speech_event = UserSpeechPartialEvent.model_validate(payload)
            else:
                speech_event = UserSpeechStoppedEvent.model_validate(payload)
        except ValidationError as exc:
            await outbound_queue.put(
                app._build_turn_error_event(
                    thread_id=str(payload.get("thread_id", "app:main") or "app:main"),
                    error=str(exc),
                )
            )
            return

        tracked_thread_ids.add(str(speech_event.thread_id or "app:main"))
        await dispatch_user_speech_event(app, speech_event, outbound_queue)
        return

    if event_type in {"browser_audio_chunk", "browser_audio_stop"}:
        try:
            if event_type == "browser_audio_chunk":
                audio_event = BrowserAudioChunkEvent.model_validate(payload)
            else:
                audio_event = BrowserAudioStopEvent.model_validate(payload)
        except ValidationError as exc:
            await outbound_queue.put(
                app._build_turn_error_event(
                    thread_id=str(payload.get("thread_id", "app:main") or "app:main"),
                    error=str(exc),
                )
            )
            return

        tracked_thread_ids.add(str(audio_event.thread_id or "app:main"))
        if event_type == "browser_audio_chunk":
            await app.handle_runtime_browser_audio_chunk(audio_event, outbound_queue)
        else:
            await app.handle_runtime_browser_audio_stop(audio_event, outbound_queue)
        return

    if event_type == "browser_camera_frame":
        try:
            frame_event = BrowserCameraFrameEvent.model_validate(payload)
            ingest_browser_camera_frame(frame_event, app.runtime_tool_context)
        except ValidationError as exc:
            app.logger.warning("Invalid browser camera frame payload: %s", exc)
        except Exception as exc:
            app.logger.warning("Failed to ingest browser camera frame: %s", exc)
        return

    await outbound_queue.put(
        app._build_turn_error_event(
            thread_id=str(payload.get("thread_id", "app:main") or "app:main"),
            error=f"Unsupported WebSocket event type: {event_type or '<empty>'}",
        )
    )


__all__ = ["handle_runtime_websocket_payload"]
