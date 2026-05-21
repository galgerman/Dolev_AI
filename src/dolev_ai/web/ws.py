"""WebSocket endpoint — streams live events to the monitoring dashboard."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from dolev_ai.events import EventBus

logger = logging.getLogger(__name__)

ws_router = APIRouter()

_event_bus: EventBus | None = None
_snapshot_fn = None  # callable() → dict, provides the initial snapshot payload


def configure(event_bus: EventBus, snapshot_fn) -> None:
    global _event_bus, _snapshot_fn
    _event_bus = event_bus
    _snapshot_fn = snapshot_fn


@ws_router.websocket("/api/stream")
async def stream(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("WS client connected")

    if _event_bus is None:
        await websocket.close(code=1011, reason="EventBus not initialized")
        return

    queue = _event_bus.subscribe()
    try:
        # Send snapshot so the UI hydrates immediately (no blank state)
        if _snapshot_fn is not None:
            snapshot = _snapshot_fn()
            await websocket.send_text(json.dumps(snapshot, default=str))

        # Forward live events
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event, default=str))
    except WebSocketDisconnect:
        logger.info("WS client disconnected")
    except Exception as e:
        logger.warning(f"WS error: {e}")
    finally:
        _event_bus.unsubscribe(queue)
