"""Tests for trading calendar — forex and equity market hours."""
import pytest
from datetime import datetime, time
from btframework.data.calendar import (
    TradingSession, SessionWindow, ForexCalendar, EquityCalendar,
)


class TestForexCalendarOpen:
    """Test forex market open/closed detection."""

    def test_monday_is_open(self):
        cal = ForexCalendar()
        # Monday 2024-01-08 10:00 UTC
        assert cal.is_market_open(datetime(2024, 1, 8, 10, 0)) is True

    def test_wednesday_is_open(self):
        cal = ForexCalendar()
        assert cal.is_market_open(datetime(2024, 1, 10, 15, 0)) is True

    def test_saturday_is_closed(self):
        cal = ForexCalendar()
        # Saturday 2024-01-06
        assert cal.is_market_open(datetime(2024, 1, 6, 10, 0)) is False

    def test_sunday_before_open_is_closed(self):
        cal = ForexCalendar()
        # Sunday 2024-01-07 at 21:00 (before 22:00 open)
        assert cal.is_market_open(datetime(2024, 1, 7, 21, 0)) is False

    def test_sunday_at_open(self):
        cal = ForexCalendar()
        # Sunday 2024-01-07 at 22:00 UTC → market opens
        assert cal.is_market_open(datetime(2024, 1, 7, 22, 0)) is True

    def test_friday_before_close(self):
        cal = ForexCalendar()
        # Friday 2024-01-12 at 21:59 → still open
        assert cal.is_market_open(datetime(2024, 1, 12, 21, 59)) is True

    def test_friday_at_close(self):
        cal = ForexCalendar()
        # Friday 2024-01-12 at 22:00 → closed
        assert cal.is_market_open(datetime(2024, 1, 12, 22, 0)) is False


class TestForexSessions:
    """Test active session detection."""

    def test_london_session(self):
        cal = ForexCalendar()
        # Wednesday 10:00 UTC → London active
        sessions = cal.active_sessions(datetime(2024, 1, 10, 10, 0))
        assert TradingSession.LONDON in sessions

    def test_tokyo_session(self):
        cal = ForexCalendar()
        # Wednesday 03:00 UTC → Tokyo active
        sessions = cal.active_sessions(datetime(2024, 1, 10, 3, 0))
        assert TradingSession.TOKYO in sessions

    def test_new_york_session(self):
        cal = ForexCalendar()
        # Wednesday 15:00 UTC → NY active
        sessions = cal.active_sessions(datetime(2024, 1, 10, 15, 0))
        assert TradingSession.NEW_YORK in sessions

    def test_sydney_session(self):
        cal = ForexCalendar()
        # Tuesday 23:00 UTC → Sydney active (22:00-07:00, crosses midnight)
        sessions = cal.active_sessions(datetime(2024, 1, 9, 23, 0))
        assert TradingSession.SYDNEY in sessions

    def test_london_ny_overlap(self):
        cal = ForexCalendar()
        # Wednesday 14:00 UTC → London (07-16) AND NY (12-21) overlap
        sessions = cal.active_sessions(datetime(2024, 1, 10, 14, 0))
        assert TradingSession.LONDON in sessions
        assert TradingSession.NEW_YORK in sessions

    def test_no_sessions_saturday(self):
        cal = ForexCalendar()
        sessions = cal.active_sessions(datetime(2024, 1, 6, 14, 0))
        assert sessions == []


class TestForexHolidays:
    """Test holiday support."""

    def test_holiday_is_closed(self):
        xmas = datetime(2024, 12, 25)
        cal = ForexCalendar(holidays=[xmas])
        assert cal.is_market_open(datetime(2024, 12, 25, 10, 0)) is False

    def test_non_holiday_still_open(self):
        xmas = datetime(2024, 12, 25)
        cal = ForexCalendar(holidays=[xmas])
        # Dec 24, 2024 is Tuesday
        assert cal.is_market_open(datetime(2024, 12, 24, 10, 0)) is True

    def test_holiday_no_active_sessions(self):
        xmas = datetime(2024, 12, 25)
        cal = ForexCalendar(holidays=[xmas])
        assert cal.active_sessions(datetime(2024, 12, 25, 10, 0)) == []


class TestForexNextOpenClose:
    """Test next_open and next_close."""

    def test_next_open_when_already_open(self):
        cal = ForexCalendar()
        ts = datetime(2024, 1, 10, 10, 0)  # Wednesday
        assert cal.next_open(ts) == ts

    def test_next_open_from_saturday(self):
        cal = ForexCalendar()
        ts = datetime(2024, 1, 6, 10, 0)  # Saturday
        result = cal.next_open(ts)
        assert result.weekday() == 6  # Sunday
        assert result.hour == 22

    def test_next_close_when_open(self):
        cal = ForexCalendar()
        ts = datetime(2024, 1, 8, 10, 0)  # Monday
        result = cal.next_close(ts)
        assert result.weekday() == 4  # Friday
        assert result.hour == 22


class TestEquityCalendar:
    """Test equity market calendar."""

    def test_open_during_hours(self):
        cal = EquityCalendar()
        # Wednesday 15:00 UTC → 10:00 ET → open
        assert cal.is_market_open(datetime(2024, 1, 10, 15, 0)) is True

    def test_closed_before_open(self):
        cal = EquityCalendar()
        # Wednesday 13:00 UTC → 08:00 ET → closed
        assert cal.is_market_open(datetime(2024, 1, 10, 13, 0)) is False

    def test_closed_after_close(self):
        cal = EquityCalendar()
        # Wednesday 22:00 UTC → 17:00 ET → closed
        assert cal.is_market_open(datetime(2024, 1, 10, 22, 0)) is False

    def test_closed_on_weekend(self):
        cal = EquityCalendar()
        assert cal.is_market_open(datetime(2024, 1, 6, 15, 0)) is False

    def test_holiday(self):
        cal = EquityCalendar(holidays=[datetime(2024, 1, 15)])
        # MLK day (Monday)
        assert cal.is_market_open(datetime(2024, 1, 15, 15, 0)) is False

    def test_no_sessions_returned(self):
        cal = EquityCalendar()
        assert cal.active_sessions(datetime(2024, 1, 10, 15, 0)) == []

    def test_next_open_from_weekend(self):
        cal = EquityCalendar()
        ts = datetime(2024, 1, 6, 10, 0)  # Saturday
        result = cal.next_open(ts)
        assert result.weekday() == 0  # Monday
        assert result.hour == 14
        assert result.minute == 30

    def test_next_close_when_open(self):
        cal = EquityCalendar()
        ts = datetime(2024, 1, 10, 15, 0)  # Wednesday, open
        result = cal.next_close(ts)
        assert result.hour == 21
        assert result.minute == 0

    def test_custom_hours(self):
        cal = EquityCalendar(
            open_time=time(8, 0),
            close_time=time(16, 30),
        )
        assert cal.is_market_open(datetime(2024, 1, 10, 8, 0)) is True
        assert cal.is_market_open(datetime(2024, 1, 10, 7, 59)) is False
        assert cal.is_market_open(datetime(2024, 1, 10, 16, 30)) is False
