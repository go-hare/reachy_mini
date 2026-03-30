"""Text input events for the resident app host."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UserTextEvent(BaseModel):
    """One browser-to-app user text event over WebSocket."""

    type: Literal["user_text"]
    text: str = Field(min_length=1)
    thread_id: str = "app:main"
    session_id: str | None = None
    user_id: str = "user"


__all__ = ["UserTextEvent"]
