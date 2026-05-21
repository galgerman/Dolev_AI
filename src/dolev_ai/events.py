"""Async pub/sub event bus — wires the agent pipeline to the WebSocket layer."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_QUEUE = 1000  # drop oldest on overflow rather than blocking


class EventBus:
    """Broadcast events to all connected WebSocket subscribers.

    Usage:
        bus = EventBus()
        q = bus.subscribe()       # in WS handler
        await bus.publish({...})  # in agent pipeline
        bus.unsubscribe(q)        # on WS disconnect
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict]] = []

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest item, then insert the new one
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    logger.warning("EventBus: subscriber queue overflow — event dropped")

    def subscriber_count(self) -> int:
        return len(self._subscribers)
