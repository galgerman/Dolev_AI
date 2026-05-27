"""US equity market hours (NYSE/NASDAQ) — DST and holidays aware.

Uses the `holidays` package (pure-Python, no compiled deps) for NYSE
holidays and stdlib `zoneinfo` for tz conversion.

Regular hours: 9:30 AM - 4:00 PM ET, Monday-Friday, excluding NYSE holidays.
Early-close days (4 in 2026: day after Thanksgiving, July 3, Dec 24, Christmas Eve)
close at 1:00 PM ET — handled by the `_EARLY_CLOSE_DATES` table.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

_OPEN = time(9, 30)
_CLOSE_REGULAR = time(16, 0)
_CLOSE_EARLY = time(13, 0)


@lru_cache(maxsize=8)
def _nyse_holidays(year: int) -> set[date]:
    cal = holidays.NYSE(years=[year])
    return set(cal.keys())


def _is_early_close(d: date) -> bool:
    """NYSE early closes: day after Thanksgiving, July 3, Christmas Eve."""
    # Day after Thanksgiving (4th Thursday of November + 1)
    nov_thursdays = [date(d.year, 11, day) for day in range(1, 30)
                     if date(d.year, 11, day).weekday() == 3]
    thanksgiving = nov_thursdays[3]
    if d == thanksgiving + timedelta(days=1):
        return True
    # Christmas Eve, if it's a weekday
    if d.month == 12 and d.day == 24 and d.weekday() < 5:
        return True
    # July 3, if it's a weekday and July 4 is not the next day's closure already
    if d.month == 7 and d.day == 3 and d.weekday() < 5:
        return True
    return False


def is_us_market_open(now: datetime | None = None) -> bool:
    """Return True if NYSE is currently open for regular trading."""
    if now is None:
        now = datetime.now(_UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_UTC)
    et = now.astimezone(_ET)

    # Weekend
    if et.weekday() >= 5:
        return False
    # Holiday
    if et.date() in _nyse_holidays(et.year):
        return False
    # Time window
    close_time = _CLOSE_EARLY if _is_early_close(et.date()) else _CLOSE_REGULAR
    return _OPEN <= et.time() < close_time


def minutes_to_close(now: datetime | None = None) -> int | None:
    """Return minutes until market close, or None if market is closed.

    Returns 0 if exactly at close. Useful for EOD flatten scheduling:
    `if minutes_to_close() is not None and minutes_to_close() <= 5: flatten()`.
    """
    if now is None:
        now = datetime.now(_UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_UTC)
    et = now.astimezone(_ET)

    if not is_us_market_open(et):
        return None

    close_time = _CLOSE_EARLY if _is_early_close(et.date()) else _CLOSE_REGULAR
    close_dt = datetime.combine(et.date(), close_time, tzinfo=_ET)
    delta = close_dt - et
    return max(0, int(delta.total_seconds() // 60))


def next_market_open(now: datetime | None = None) -> datetime:
    """Return the next US market open time in UTC."""
    if now is None:
        now = datetime.now(_UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_UTC)
    et = now.astimezone(_ET)

    candidate = datetime.combine(et.date(), _OPEN, tzinfo=_ET)
    # If today's open already passed, look at tomorrow
    if et.time() >= _OPEN:
        candidate = candidate + timedelta(days=1)

    # Skip weekends and holidays
    while (candidate.weekday() >= 5
           or candidate.date() in _nyse_holidays(candidate.year)):
        candidate = candidate + timedelta(days=1)

    return candidate.astimezone(_UTC)
