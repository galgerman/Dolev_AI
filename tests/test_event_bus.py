"""Tests for EventBus."""
import asyncio
import pytest
from dolev_ai.events import EventBus


@pytest.mark.asyncio
async def test_publish_broadcasts_to_all_subscribers():
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    await bus.publish({"type": "test", "val": 42})
    assert q1.get_nowait() == {"type": "test", "val": 42}
    assert q2.get_nowait() == {"type": "test", "val": 42}


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    await bus.publish({"type": "test"})
    assert q.empty()


@pytest.mark.asyncio
async def test_subscriber_count():
    bus = EventBus()
    assert bus.subscriber_count() == 0
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    assert bus.subscriber_count() == 2
    bus.unsubscribe(q1)
    assert bus.subscriber_count() == 1


@pytest.mark.asyncio
async def test_full_queue_drops_oldest():
    """When a subscriber queue is full, oldest event is dropped to make room."""
    from dolev_ai.events import _MAX_QUEUE
    bus = EventBus()
    q = bus.subscribe()
    # Fill the queue
    for i in range(_MAX_QUEUE):
        q.put_nowait({"i": i})
    # Publish one more — should not raise, and queue size stays bounded
    await bus.publish({"i": "new"})
    assert q.qsize() <= _MAX_QUEUE


@pytest.mark.asyncio
async def test_multiple_publishes_ordered():
    bus = EventBus()
    q = bus.subscribe()
    for i in range(5):
        await bus.publish({"seq": i})
    results = []
    while not q.empty():
        results.append(q.get_nowait()["seq"])
    assert results == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_subscribe_replays_current_collection_state():
    bus = EventBus()
    await bus.publish({"type": "collection.account_started", "handle": "deitaone", "index": 1, "total": 2})

    q = bus.subscribe()

    assert q.get_nowait() == {"type": "collection.account_started", "handle": "deitaone", "index": 1, "total": 2}


@pytest.mark.asyncio
async def test_collection_started_clears_previous_provisional_replay():
    bus = EventBus()
    await bus.publish({"type": "ticker.discovered", "ticker": "NVDA", "voices": 1, "tweet_count": 1})
    await bus.publish({"type": "graph.edge_added", "author": "deitaone", "ticker": "NVDA", "weight": 1.0})
    await bus.publish({"type": "collection.started", "total": 3, "completed": 0})

    q = bus.subscribe()

    assert q.get_nowait() == {"type": "collection.started", "total": 3, "completed": 0}
    assert q.empty()
