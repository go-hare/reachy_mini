"""Helpers for dispatching app-side input events onto the runtime loop."""

from __future__ import annotations

import asyncio
from typing import Any

from .speech import BrowserSpeechEvent
from .text import UserTextEvent


async def dispatch_user_turn(
    app: Any,
    event: UserTextEvent,
    outbound_queue: asyncio.Queue[dict[str, Any]],
) -> None:
    """Dispatch one resident-runtime turn without waiting for kernel completion."""

    runtime = app.runtime
    thread_id = str(event.thread_id or "app:main")
    runtime_loop = app.runtime_loop
    if runtime is None or runtime_loop is None or not app.runtime_ready.is_set():
        await outbound_queue.put(
            app._build_turn_error_event(
                thread_id=thread_id,
                error="Runtime is not ready yet.",
            )
        )
        return

    try:
        future = asyncio.run_coroutine_threadsafe(
            dispatch_user_turn_on_runtime_loop(
                app,
                thread_id=thread_id,
                session_id=str(event.session_id or thread_id),
                user_id=str(event.user_id or "user"),
                user_text=str(event.text),
                outbound_queue=outbound_queue,
                outbound_loop=asyncio.get_running_loop(),
            ),
            runtime_loop,
        )
        await asyncio.wrap_future(future)
    except Exception as exc:
        await outbound_queue.put(
            app._build_turn_error_event(
                thread_id=thread_id,
                error=str(exc),
            )
        )


async def dispatch_user_speech_event(
    app: Any,
    event: BrowserSpeechEvent,
    outbound_queue: asyncio.Queue[dict[str, Any]],
) -> None:
    """Dispatch one speech lifecycle event onto the resident runtime."""

    runtime = app.runtime
    thread_id = str(event.thread_id or "app:main")
    runtime_loop = app.runtime_loop
    if runtime is None or runtime_loop is None or not app.runtime_ready.is_set():
        await outbound_queue.put(
            app._build_turn_error_event(
                thread_id=thread_id,
                error="Runtime is not ready yet.",
            )
        )
        return

    try:
        future = asyncio.run_coroutine_threadsafe(
            dispatch_user_speech_event_on_runtime_loop(
                app,
                event_type=str(event.type),
                thread_id=thread_id,
                session_id=str(event.session_id or thread_id),
                user_id=str(event.user_id or "user"),
                user_text=str(event.text or ""),
                outbound_queue=outbound_queue,
                outbound_loop=asyncio.get_running_loop(),
            ),
            runtime_loop,
        )
        await asyncio.wrap_future(future)
    except Exception as exc:
        await outbound_queue.put(
            app._build_turn_error_event(
                thread_id=thread_id,
                error=str(exc),
            )
        )


async def dispatch_user_turn_on_runtime_loop(
    app: Any,
    *,
    thread_id: str,
    session_id: str,
    user_id: str,
    user_text: str,
    outbound_queue: asyncio.Queue[dict[str, Any]],
    outbound_loop: asyncio.AbstractEventLoop,
) -> None:
    """Dispatch one turn on the runtime loop and return after front delivery."""

    runtime = app.runtime
    if runtime is None:
        raise RuntimeError("Runtime is not ready yet.")

    surface_state_handler = _build_surface_state_handler(
        app,
        thread_id=thread_id,
        outbound_queue=outbound_queue,
        outbound_loop=outbound_loop,
    )
    await runtime.handle_user_turn(
        thread_id=thread_id,
        session_id=session_id,
        user_id=user_id,
        user_text=user_text,
        surface_state_handler=surface_state_handler,
        final_reply_handler=app.play_runtime_reply_audio,
    )


async def dispatch_user_speech_event_on_runtime_loop(
    app: Any,
    *,
    event_type: str,
    thread_id: str,
    session_id: str,
    user_id: str,
    user_text: str,
    outbound_queue: asyncio.Queue[dict[str, Any]],
    outbound_loop: asyncio.AbstractEventLoop,
) -> None:
    """Dispatch one speech lifecycle event on the runtime loop."""

    runtime = app.runtime
    if runtime is None:
        raise RuntimeError("Runtime is not ready yet.")

    surface_state_handler = _build_surface_state_handler(
        app,
        thread_id=thread_id,
        outbound_queue=outbound_queue,
        outbound_loop=outbound_loop,
    )

    if event_type == "user_speech_started":
        await runtime.handle_user_speech_started(
            thread_id=thread_id,
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
            surface_state_handler=surface_state_handler,
        )
        return

    if event_type == "user_speech_partial":
        await runtime.handle_user_speech_partial(
            thread_id=thread_id,
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
            surface_state_handler=surface_state_handler,
        )
        return

    if event_type == "user_speech_stopped":
        await runtime.handle_user_speech_stopped(
            thread_id=thread_id,
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
            surface_state_handler=surface_state_handler,
        )
        return

    raise RuntimeError(f"Unsupported speech event type: {event_type or '<empty>'}")


def _build_surface_state_handler(
    app: Any,
    *,
    thread_id: str,
    outbound_queue: asyncio.Queue[dict[str, Any]],
    outbound_loop: asyncio.AbstractEventLoop,
):
    async def publish_event(event: dict[str, Any]) -> None:
        put_future = asyncio.run_coroutine_threadsafe(
            outbound_queue.put(event),
            outbound_loop,
        )
        await asyncio.wrap_future(put_future)

    async def surface_state_handler(state: dict[str, Any]) -> None:
        app.apply_runtime_surface_state(state)
        await publish_event(
            {
                "type": "surface_state",
                "thread_id": thread_id,
                "state": dict(state),
            }
        )

    return surface_state_handler


__all__ = [
    "dispatch_user_speech_event",
    "dispatch_user_speech_event_on_runtime_loop",
    "dispatch_user_turn",
    "dispatch_user_turn_on_runtime_loop",
]
