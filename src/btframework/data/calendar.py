"""Trading calendar — market hours and session tracking.

Supports forex (24/5 with sessions) and equity (configurable hours).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Protocol, runtime_checkable


class TradingSession(Enum):
    """Major FX trading sessions."""
    SYDNEY = "sydney"
    TOKYO = "tokyo"
    LONDON = "london"
    NEW_YORK = "new_york"


@dataclass(frozen=True)
class SessionWindow:
    """Time window for a trading session."""
    session: TradingSession
    open_utc: time   # e.g. time(7, 0) for London
    close_utc: time  # e.g. time(16, 0) for London

    @property
    def crosses_midnight(self) -> bool:
        return self.close_utc <= self.open_utc


@dataclass(frozen=True)
class MarketHours:
    """Weekly open/close for a market."""
    weekly_open_utc: tuple[int, time]   # (weekday, time) — 6=Sunday
    weekly_close_utc: tuple[int, time]  # (weekday, time) — 4=Friday
    sessions: list[SessionWindow] = field(default_factory=list)


@runtime_checkable
class TradingCalendar(Protocol):
    """Protocol for market calendars."""

    def is_market_open(self, timestamp: datetime) -> bool:
        ...

    def active_sessions(self, timestamp: datetime) -> list[TradingSession]:
        ...

    def next_open(self, timestamp: datetime) -> datetime:
        ...

    def next_close(self, timestamp: datetime) -> datetime:
        ...


# --- Forex Calendar ---

# Default FX sessions (UTC hours)
_DEFAULT_FX_SESSIONS = [
    SessionWindow(TradingSession.SYDNEY,   time(22, 0), time(7, 0)),   # crosses midnight
    SessionWindow(TradingSession.TOKYO,    time(0, 0),  time(9, 0)),
    SessionWindow(TradingSession.LONDON,   time(7, 0),  time(16, 0)),
    SessionWindow(TradingSession.NEW_YORK, time(12, 0), time(21, 0)),
]


class ForexCalendar:
    """Forex market calendar: Sunday 22:00 UTC → Friday 22:00 UTC.

    Sessions: Sydney 22-07, Tokyo 00-09, London 07-16, NY 12-21.
    """

    def __init__(
        self,
        sessions: list[SessionWindow] | None = None,
        holidays: list[datetime] | None = None,
    ):
        self._sessions = sessions or list(_DEFAULT_FX_SESSIONS)
        self._holidays: set[datetime] = set()
        for h in (holidays or []):
            # Normalize to date at midnight
            self._holidays.add(datetime(h.year, h.month, h.day))

    def is_market_open(self, timestamp: datetime) -> bool:
        """Forex open Sunday 22:00 UTC through Friday 22:00 UTC, minus holidays."""
        if self._is_holiday(timestamp):
            return False
        return self._in_weekly_window(timestamp)

    def active_sessions(self, timestamp: datetime) -> list[TradingSession]:
        """Return sessions active at the given UTC time."""
        if not self.is_market_open(timestamp):
            return []
        t = timestamp.time()
        active = []
        for sw in self._sessions:
            if self._time_in_window(t, sw.open_utc, sw.close_utc, sw.crosses_midnight):
                active.append(sw.session)
        return active

    def next_open(self, timestamp: datetime) -> datetime:
        """Next market open after the given timestamp."""
        if self.is_market_open(timestamp):
            return timestamp
        # Find next Sunday 22:00 UTC
        dt = timestamp.replace(hour=22, minute=0, second=0, microsecond=0)
        # Move to next Sunday
        days_until_sunday = (6 - dt.weekday()) % 7
        if days_until_sunday == 0 and timestamp >= dt:
            days_until_sunday = 7
        dt = dt + timedelta(days=days_until_sunday)
        # Skip holidays
        while self._is_holiday(dt):
            dt += timedelta(days=7)
        return dt

    def next_close(self, timestamp: datetime) -> datetime:
        """Next market close after the given timestamp."""
        if not self.is_market_open(timestamp):
            # Already closed — find next open then next close after that
            next_op = self.next_open(timestamp)
            return self._friday_close(next_op)
        return self._friday_close(timestamp)

    def _friday_close(self, timestamp: datetime) -> datetime:
        """Find the Friday 22:00 UTC close for the week containing timestamp."""
        dt = timestamp.replace(hour=22, minute=0, second=0, microsecond=0)
        weekday = dt.weekday()
        # Friday = 4
        days_until_friday = (4 - weekday) % 7
        if days_until_friday == 0 and timestamp >= dt:
            days_until_friday = 7
        return dt + timedelta(days=days_until_friday)

    def _in_weekly_window(self, timestamp: datetime) -> bool:
        """Check if timestamp falls within Sunday 22:00 – Friday 22:00 UTC."""
        weekday = timestamp.weekday()  # 0=Mon ... 6=Sun
        t = timestamp.time()

        # Saturday (5): always closed
        if weekday == 5:
            return False
        # Sunday (6): open only from 22:00
        if weekday == 6:
            return t >= time(22, 0)
        # Friday (4): open until 22:00
        if weekday == 4:
            return t < time(22, 0)
        # Mon-Thu: always open
        return True

    def _is_holiday(self, timestamp: datetime) -> bool:
        day = datetime(timestamp.year, timestamp.month, timestamp.day)
        return day in self._holidays

    @staticmethod
    def _time_in_window(t: time, open_t: time, close_t: time, crosses_midnight: bool) -> bool:
        if crosses_midnight:
            return t >= open_t or t < close_t
        return open_t <= t < close_t


class EquityCalendar:
    """Configurable equity market calendar.

    Default: Mon-Fri 09:30-16:00 ET (14:30-21:00 UTC).
    """

    def __init__(
        self,
        open_time: time = time(14, 30),   # 09:30 ET in UTC
        close_time: time = time(21, 0),   # 16:00 ET in UTC
        trading_days: list[int] | None = None,  # weekdays, 0=Mon
        holidays: list[datetime] | None = None,
    ):
        self._open_time = open_time
        self._close_time = close_time
        self._trading_days = set(trading_days or [0, 1, 2, 3, 4])
        self._holidays: set[datetime] = set()
        for h in (holidays or []):
            self._holidays.add(datetime(h.year, h.month, h.day))

    def is_market_open(self, timestamp: datetime) -> bool:
        if timestamp.weekday() not in self._trading_days:
            return False
        if self._is_holiday(timestamp):
            return False
        t = timestamp.time()
        return self._open_time <= t < self._close_time

    def active_sessions(self, timestamp: datetime) -> list[TradingSession]:
        """Equity markets don't have FX sessions — return empty list."""
        return []

    def next_open(self, timestamp: datetime) -> datetime:
        if self.is_market_open(timestamp):
            return timestamp
        dt = timestamp.replace(
            hour=self._open_time.hour,
            minute=self._open_time.minute,
            second=0,
            microsecond=0,
        )
        if dt <= timestamp:
            dt += timedelta(days=1)
        while dt.weekday() not in self._trading_days or self._is_holiday(dt):
            dt += timedelta(days=1)
        return dt

    def next_close(self, timestamp: datetime) -> datetime:
        if self.is_market_open(timestamp):
            return timestamp.replace(
                hour=self._close_time.hour,
                minute=self._close_time.minute,
                second=0,
                microsecond=0,
            )
        next_op = self.next_open(timestamp)
        return next_op.replace(
            hour=self._close_time.hour,
            minute=self._close_time.minute,
            second=0,
            microsecond=0,
        )

    def _is_holiday(self, timestamp: datetime) -> bool:
        day = datetime(timestamp.year, timestamp.month, timestamp.day)
        return day in self._holidays
