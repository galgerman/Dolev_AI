"""Tests for US market hours utility."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dolev_ai.utils.market_hours import (
    is_us_market_open,
    minutes_to_close,
    next_market_open,
)

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


def _et(year: int, month: int, day: int, h: int, m: int) -> datetime:
    """Helper: build an ET datetime, return as UTC."""
    return datetime(year, month, day, h, m, tzinfo=_ET).astimezone(_UTC)


# ── is_us_market_open ─────────────────────────────────────────────────────────

class TestIsMarketOpen:
    def test_open_at_930am(self):
        # Tue 2026-06-09 9:30 AM ET (a regular trading day)
        assert is_us_market_open(_et(2026, 6, 9, 9, 30)) is True

    def test_closed_at_929am(self):
        assert is_us_market_open(_et(2026, 6, 9, 9, 29)) is False

    def test_open_at_355pm(self):
        assert is_us_market_open(_et(2026, 6, 9, 15, 55)) is True

    def test_closed_at_400pm(self):
        # 4:00 PM exact is the close — interval is half-open [9:30, 16:00)
        assert is_us_market_open(_et(2026, 6, 9, 16, 0)) is False

    def test_closed_at_401pm(self):
        assert is_us_market_open(_et(2026, 6, 9, 16, 1)) is False

    def test_closed_saturday(self):
        # Sat 2026-06-13
        assert is_us_market_open(_et(2026, 6, 13, 11, 0)) is False

    def test_closed_sunday(self):
        # Sun 2026-06-14
        assert is_us_market_open(_et(2026, 6, 14, 11, 0)) is False

    def test_closed_july4(self):
        # 2026-07-03 is the observed Independence Day (since July 4 is Saturday)
        assert is_us_market_open(_et(2026, 7, 3, 11, 0)) is False

    def test_closed_thanksgiving(self):
        # 2026-11-26
        assert is_us_market_open(_et(2026, 11, 26, 11, 0)) is False

    def test_closed_memorial_day(self):
        # 2026-05-25 (today, in the dev environment)
        assert is_us_market_open(_et(2026, 5, 25, 11, 0)) is False

    def test_early_close_day_after_thanksgiving(self):
        # 2026-11-27 — open at 9:30, closes at 13:00 ET
        assert is_us_market_open(_et(2026, 11, 27, 11, 0)) is True   # open
        assert is_us_market_open(_et(2026, 11, 27, 13, 0)) is False  # early close
        assert is_us_market_open(_et(2026, 11, 27, 15, 0)) is False  # past close


# ── minutes_to_close ──────────────────────────────────────────────────────────

class TestMinutesToClose:
    def test_returns_none_when_closed(self):
        assert minutes_to_close(_et(2026, 6, 13, 11, 0)) is None  # Saturday

    def test_at_355pm_returns_5(self):
        assert minutes_to_close(_et(2026, 6, 9, 15, 55)) == 5

    def test_at_330pm_returns_30(self):
        assert minutes_to_close(_et(2026, 6, 9, 15, 30)) == 30

    def test_at_930am_returns_390(self):
        # 9:30 AM to 4:00 PM = 6.5h = 390 min
        assert minutes_to_close(_et(2026, 6, 9, 9, 30)) == 390

    def test_early_close_returns_correct(self):
        # 12:55 PM on early-close day → 5 minutes to 1:00 PM close
        assert minutes_to_close(_et(2026, 11, 27, 12, 55)) == 5


# ── next_market_open ──────────────────────────────────────────────────────────

class TestNextOpen:
    def test_from_weekend(self):
        # Sat → next open is Monday 9:30 ET
        nxt = next_market_open(_et(2026, 6, 13, 11, 0))
        assert nxt.astimezone(_ET).date().weekday() == 0  # Monday
        assert nxt.astimezone(_ET).hour == 9
        assert nxt.astimezone(_ET).minute == 30

    def test_from_holiday(self):
        # Memorial Day 2026-05-25 → next open is Tuesday 2026-05-26
        nxt = next_market_open(_et(2026, 5, 25, 11, 0))
        et = nxt.astimezone(_ET)
        assert et.month == 5 and et.day == 26
