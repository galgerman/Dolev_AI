"""X (Twitter) login flow triggered from the web UI."""
from __future__ import annotations

import asyncio
import logging
import pathlib
from typing import Literal

logger = logging.getLogger(__name__)

LoginState = Literal["idle", "opening", "waiting", "complete", "error"]

_state: LoginState = "idle"
_profile_dir: pathlib.Path | None = None
_event_bus = None  # injected by server.py

_SCRIPTS_DIR = pathlib.Path(__file__).parent.parent.parent.parent / "scripts"


def configure(profile_dir: pathlib.Path, event_bus) -> None:
    global _profile_dir, _event_bus
    _profile_dir = profile_dir
    _event_bus = event_bus


def is_logged_in() -> bool:
    if _profile_dir is None or not _profile_dir.exists():
        return False
    return any(_profile_dir.iterdir())


def get_state() -> LoginState:
    return _state


async def start_login() -> bool:
    """Kick off the Playwright login flow in a background task. Returns False if already running."""
    global _state
    if _state in ("opening", "waiting"):
        return False
    _state = "opening"
    await _publish_state()
    asyncio.create_task(_run_login())
    return True


async def _run_login() -> None:
    global _state
    try:
        if _profile_dir is None:
            raise RuntimeError("profile_dir not configured")
        _profile_dir.mkdir(parents=True, exist_ok=True)
        _state = "waiting"
        await _publish_state()
        logger.info("X login: opening browser — log in and close it")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_login, str(_profile_dir))

        _state = "complete"
        logger.info("X login: session saved")
    except Exception as e:
        _state = "error"
        logger.error(f"X login failed: {e}", exc_info=True)
    finally:
        await _publish_state()


_BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_browser() -> str:
    import os
    for path in _BROWSER_CANDIDATES:
        if os.path.exists(path):
            return path
    raise RuntimeError(
        "No supported browser found. Install Microsoft Edge or Google Chrome."
    )


def _sync_login(profile_dir: str) -> None:
    from playwright.sync_api import sync_playwright
    exe = _find_browser()
    logger.info(f"X login: using browser at {exe}")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile_dir,
            executable_path=exe,
            headless=False,
            args=["--start-maximized"],
        )
        page = ctx.new_page()
        page.goto("https://x.com/login")
        ctx.wait_for_event("close", timeout=0)


async def _publish_state() -> None:
    if _event_bus is not None:
        await _event_bus.publish({
            "type": "auth.x.status_changed",
            "state": _state,
            "logged_in": is_logged_in(),
        })
