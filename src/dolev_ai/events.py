"""Async pub/sub event bus — wires the agent pipeline to the WebSocket layer."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_QUEUE = 1000  # drop oldest on overflow rather than blocking
_MAX_REPLAY_TWEETS = 100


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
        self._replay: dict[tuple[Any, ...], dict[str, Any]] = {}

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=_MAX_QUEUE)
        for event in self.replay_events():
            q.put_nowait(event)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def publish(self, event: dict[str, Any]) -> None:
        self._remember(event)
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

    def replay_events(self) -> list[dict[str, Any]]:
        collection = self._replay.get(("collection",))
        events = [event for key, event in self._replay.items() if key != ("collection",)]
        if collection is not None:
            return [collection, *events]
        return events

    def _remember(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if not isinstance(event_type, str):
            return

        if event_type == "collection.started":
            self._replay.clear()
            self._replay[("collection",)] = event
            return

        if event_type.startswith("collection."):
            self._replay[("collection",)] = event
            return

        if event_type == "ticker.discovered" and event.get("ticker"):
            self._replay[("ticker", event["ticker"])] = event
            return

        if event_type == "graph.edge_added" and event.get("author") and event.get("ticker"):
            self._replay[("edge", event["author"], event["ticker"])] = event
            return

        if event_type == "tweet.ingested" and event.get("id"):
            self._replay[("tweet", event["id"])] = event
            self._trim_replayed_tweets()

    def _trim_replayed_tweets(self) -> None:
        tweet_keys = [key for key in self._replay if key and key[0] == "tweet"]
        for key in tweet_keys[:-_MAX_REPLAY_TWEETS]:
            self._replay.pop(key, None)
