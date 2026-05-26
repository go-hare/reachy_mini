"""Fan-out output bus for the v4 pipeline session."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

FrameFilter = Callable[[Any], bool]


@dataclass
class OutputSubscription:
    """One subscriber's view onto the output bus."""

    id: int
    queue: asyncio.Queue[Any]
    filter: FrameFilter | None = None
    closed: bool = field(default=False)


class OutputBus:
    """Multi-subscriber fan-out for pipeline output frames.

    Frames are immutable dataclasses, so we publish the same instance to every
    subscriber and let them dequeue at their own pace. Subscribers that fall
    behind keep their own backlog in their queue; the bus does not drop frames.
    """

    def __init__(self, *, queue_maxsize: int = 0) -> None:
        """Create an empty output bus."""
        self._queue_maxsize = queue_maxsize
        self._subs: dict[int, OutputSubscription] = {}
        self._lock = asyncio.Lock()
        self._next_id = itertools.count(1)

    def subscribe(
        self,
        *,
        filter: FrameFilter | None = None,
        queue_maxsize: int | None = None,
    ) -> OutputSubscription:
        """Register a new subscriber and return its handle."""
        sub = OutputSubscription(
            id=next(self._next_id),
            queue=asyncio.Queue(
                maxsize=self._queue_maxsize if queue_maxsize is None else queue_maxsize
            ),
            filter=filter,
        )
        self._subs[sub.id] = sub
        return sub

    def unsubscribe(self, sub: OutputSubscription) -> None:
        """Unregister a subscriber and mark it closed."""
        sub.closed = True
        self._subs.pop(sub.id, None)

    async def publish(self, frame: Any) -> None:
        """Publish one frame to every active subscriber."""
        async with self._lock:
            targets = list(self._subs.values())
        for sub in targets:
            if sub.closed:
                continue
            if sub.filter is not None and not sub.filter(frame):
                continue
            await sub.queue.put(frame)

    async def publish_many(self, frames: list[Any]) -> None:
        """Publish a list of frames preserving order."""
        for frame in frames:
            await self.publish(frame)

    def subscriber_count(self) -> int:
        """Return the number of active subscribers."""
        return len(self._subs)

    async def close(self) -> None:
        """Close every subscription and prevent further publication."""
        async with self._lock:
            subs = list(self._subs.values())
            self._subs.clear()
        for sub in subs:
            sub.closed = True
