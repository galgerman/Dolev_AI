"""FastAPI application — REST API + WebSocket + static SPA serving."""
from __future__ import annotations

import json
import logging
import pathlib
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from dolev_ai.events import EventBus
from dolev_ai.web.api import auth_router, configure as configure_api
from dolev_ai.web.api import router as api_router
from dolev_ai.web.ws import configure as configure_ws
from dolev_ai.web.ws import ws_router

logger = logging.getLogger(__name__)

DIST_DIR = pathlib.Path(__file__).parent.parent.parent.parent / "web" / "dist"


def create_app(
    event_bus: EventBus,
    session_factory,
    threshold: float = 5.0,
    started_at: datetime | None = None,
    profile_dir: pathlib.Path | None = None,
) -> FastAPI:
    if started_at is None:
        started_at = datetime.utcnow()

    from dolev_ai.web import auth as auth_mod
    _default_profile = pathlib.Path(__file__).parent.parent.parent.parent / "browser_profile"
    auth_mod.configure(profile_dir or _default_profile, event_bus)

    configure_api(
        started_at=started_at,
        threshold=threshold,
        get_session=session_factory,
        event_bus=event_bus,
    )

    def _snapshot() -> dict:
        """Build the initial snapshot event sent to new WS connections."""
        from dolev_ai.web.api import tickers_live, signals_recent, graph_snapshot
        try:
            with session_factory() as db:
                tickers = tickers_live(limit=20, db=db)
                signals = signals_recent(limit=10, db=db)
                graph = graph_snapshot(db=db)
            return {
                "type": "snapshot",
                "top_tickers": [t.model_dump() for t in tickers],
                "recent_signals": [s.model_dump() for s in signals],
                "graph": graph.model_dump(),
            }
        except Exception as e:
            logger.warning(f"Snapshot build failed: {e}")
            return {"type": "snapshot", "top_tickers": [], "recent_signals": [], "graph": {"nodes": [], "edges": []}}

    configure_ws(event_bus=event_bus, snapshot_fn=_snapshot)

    app = FastAPI(title="Dolev AI Monitor", docs_url="/api/docs")

    # Allow Vite dev server (port 5173) in development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    app.include_router(auth_router)
    app.include_router(ws_router)

    # Serve built SPA — graceful if web/dist doesn't exist yet (dev mode)
    if DIST_DIR.exists():
        app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

        @app.get("/")
        async def index():
            from fastapi.responses import FileResponse
            return FileResponse(DIST_DIR / "index.html")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            from fastapi.responses import FileResponse
            return FileResponse(DIST_DIR / "index.html")
    else:
        @app.get("/")
        async def no_dist():
            from fastapi.responses import HTMLResponse
            return HTMLResponse(
                "<h2>Frontend not built yet.</h2>"
                "<p>Run <code>cd web &amp;&amp; npm run build</code>, then restart the agent.</p>"
                "<p><a href='/api/docs'>API docs</a></p>"
            )

    return app
