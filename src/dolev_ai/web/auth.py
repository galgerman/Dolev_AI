"""X (Twitter) login flow triggered from the web UI."""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sqlite3
import subprocess
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
    return _has_x_auth_cookie(_profile_dir)


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

        if is_logged_in():
            _state = "complete"
            logger.info("X login: session saved")
        else:
            _state = "error"
            logger.warning("X login: browser closed without a saved X auth session")
    except Exception as e:
        _state = "error"
        logger.error(f"X login failed: {e}", exc_info=True)
    finally:
        await _publish_state()


_BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_browser() -> str:
    for path in _BROWSER_CANDIDATES:
        if os.path.exists(path):
            return path
    raise RuntimeError(
        "No supported browser found. Install Microsoft Edge or Google Chrome."
    )


def _sync_login(profile_dir: str) -> None:
    exe = _find_browser()
    logger.info(f"X login: using browser at {exe}")
    _remove_stale_profile_locks(profile_dir)
    process = subprocess.Popen(_browser_login_args(exe, profile_dir))
    process.wait()


def _browser_login_args(exe: str, profile_dir: str) -> list[str]:
    return [
        exe,
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--new-window",
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
        "https://x.com/i/flow/login",
    ]


def _remove_stale_profile_locks(profile_dir: str) -> None:
    profile_path = pathlib.Path(profile_dir)
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock_path = profile_path / name
        try:
            if lock_path.exists() or lock_path.is_symlink():
                lock_path.unlink()
                logger.info(f"X login: removed stale Chromium profile lock {lock_path}")
        except OSError as e:
            logger.warning(f"X login: could not remove profile lock {lock_path}: {e}")


def _has_x_auth_cookie(profile_dir: pathlib.Path) -> bool:
    for cookie_db in _cookie_db_candidates(profile_dir):
        if _cookie_db_has_x_auth(cookie_db):
            return True
    return False


def _cookie_db_candidates(profile_dir: pathlib.Path) -> list[pathlib.Path]:
    return [
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Default" / "Cookies",
    ]


def _cookie_db_has_x_auth(cookie_db: pathlib.Path) -> bool:
    if not cookie_db.exists():
        return False
    try:
        with sqlite3.connect(f"file:{cookie_db}?mode=ro", uri=True) as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM cookies
                WHERE name = 'auth_token'
                  AND (
                    host_key = 'x.com'
                    OR host_key = '.x.com'
                    OR host_key = 'twitter.com'
                    OR host_key = '.twitter.com'
                  )
                LIMIT 1
                """
            ).fetchone()
            return row is not None
    except sqlite3.Error as e:
        logger.debug(f"X login: could not inspect cookie DB {cookie_db}: {e}")
        return False


async def _publish_state() -> None:
    if _event_bus is not None:
        await _event_bus.publish({
            "type": "auth.x.status_changed",
            "state": _state,
            "logged_in": is_logged_in(),
        })
