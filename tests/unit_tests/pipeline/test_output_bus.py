"""Tests for the OutputBus fan-out."""

from __future__ import annotations

import asyncio

import pytest

from reachy_mini.pipeline.output_bus import OutputBus


@pytest.mark.asyncio
async def test_publish_fans_out_to_all_subscribers() -> None:
    """Every active subscriber receives every published frame."""
    bus = OutputBus()
    a = bus.subscribe()
    b = bus.subscribe()

    await bus.publish("frame-1")
    await bus.publish("frame-2")

    assert a.queue.qsize() == 2
    assert b.queue.qsize() == 2
    assert await a.queue.get() == "frame-1"
    assert await a.queue.get() == "frame-2"
    assert await b.queue.get() == "frame-1"
    assert await b.queue.get() == "frame-2"


@pytest.mark.asyncio
async def test_filter_drops_non_matching_frames() -> None:
    """Subscribers with a filter only see matching frames."""
    bus = OutputBus()
    sub = bus.subscribe(filter=lambda frame: isinstance(frame, int))

    await bus.publish("text")
    await bus.publish(42)

    assert sub.queue.qsize() == 1
    assert await sub.queue.get() == 42


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    """Unsubscribed subscribers stop receiving frames."""
    bus = OutputBus()
    sub = bus.subscribe()

    await bus.publish("first")
    bus.unsubscribe(sub)
    await bus.publish("second")

    assert sub.closed is True
    assert sub.queue.qsize() == 1


@pytest.mark.asyncio
async def test_publish_many_preserves_order() -> None:
    """publish_many delivers frames in given order to all subscribers."""
    bus = OutputBus()
    sub = bus.subscribe()

    await bus.publish_many(["a", "b", "c"])

    received: list[str] = []
    while not sub.queue.empty():
        received.append(await sub.queue.get())
    assert received == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_close_marks_subscriptions_closed() -> None:
    """close() empties the subscriber set and marks subs closed."""
    bus = OutputBus()
    sub = bus.subscribe()

    await bus.close()

    assert sub.closed is True
    assert bus.subscriber_count() == 0


@pytest.mark.asyncio
async def test_subscriber_with_full_bounded_queue_blocks_publisher() -> None:
    """Bounded queues exert backpressure on publish."""
    bus = OutputBus()
    sub = bus.subscribe(queue_maxsize=1)

    await bus.publish("first")

    publish_task = asyncio.create_task(bus.publish("second"))
    await asyncio.sleep(0.05)
    assert not publish_task.done()

    await sub.queue.get()
    await asyncio.wait_for(publish_task, timeout=0.5)
    assert sub.queue.qsize() == 1
