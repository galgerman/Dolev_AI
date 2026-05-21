"""Tests for the X auth helper."""
import sqlite3
from unittest.mock import MagicMock

from dolev_ai.web import auth


def test_non_empty_profile_without_x_auth_cookie_is_not_logged_in(tmp_path):
    profile_dir = tmp_path / "browser_profile"
    (profile_dir / "Default" / "Network").mkdir(parents=True)
    (profile_dir / "Local State").write_text("{}", encoding="utf-8")

    auth.configure(profile_dir, event_bus=None)

    assert auth.is_logged_in() is False


def test_profile_with_x_auth_cookie_is_logged_in(tmp_path):
    profile_dir = tmp_path / "browser_profile"
    cookie_dir = profile_dir / "Default" / "Network"
    cookie_dir.mkdir(parents=True)
    db_path = cookie_dir / "Cookies"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT)")
        conn.execute(
            "INSERT INTO cookies (host_key, name) VALUES (?, ?)",
            (".x.com", "auth_token"),
        )

    auth.configure(profile_dir, event_bus=None)

    assert auth.is_logged_in() is True


def test_stale_chromium_lock_files_are_removed_before_login(tmp_path):
    profile_dir = tmp_path / "browser_profile"
    profile_dir.mkdir()
    stale_lock = profile_dir / "SingletonLock"
    stale_socket = profile_dir / "SingletonSocket"
    unrelated = profile_dir / "Local State"
    stale_lock.write_text("", encoding="utf-8")
    stale_socket.write_text("", encoding="utf-8")
    unrelated.write_text("{}", encoding="utf-8")

    auth._remove_stale_profile_locks(str(profile_dir))

    assert not stale_lock.exists()
    assert not stale_socket.exists()
    assert unrelated.exists()


def test_sync_login_launches_normal_browser_with_project_profile(monkeypatch, tmp_path):
    launched = {}
    process = MagicMock()

    def fake_popen(args):
        launched["args"] = args
        return process

    monkeypatch.setattr(auth, "_find_browser", lambda: r"C:\Browser\chrome.exe")
    monkeypatch.setattr(auth, "_remove_stale_profile_locks", lambda profile_dir: launched.setdefault("profile_dir", profile_dir))
    monkeypatch.setattr(auth.subprocess, "Popen", fake_popen)

    auth._sync_login(str(tmp_path))

    assert launched["profile_dir"] == str(tmp_path)
    assert launched["args"][0] == r"C:\Browser\chrome.exe"
    assert f"--user-data-dir={tmp_path}" in launched["args"]
    assert "--new-window" in launched["args"]
    assert "https://x.com/i/flow/login" in launched["args"]
    process.wait.assert_called_once_with()


def test_find_browser_prefers_chrome_over_edge(monkeypatch):
    available = {
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    }

    monkeypatch.setattr(auth.os.path, "exists", lambda path: path in available)

    assert auth._find_browser() == r"C:\Program Files\Google\Chrome\Application\chrome.exe"
