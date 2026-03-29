"""Tests for variable spread middleware."""
import pytest
from datetime import datetime
from decimal import Decimal
from btframework.data.calendar import TradingSession, ForexCalendar
from btframework.execution.middlewares.spread import (
    SessionSpreadConfig, SpreadProfile, VariableSpreadMiddleware,
)
from btframework.execution.fill import Fill
from btframework.core.middleware import ExecutionContext
from btframework.types import Side


def _make_fill(side=Side.BUY, price="1.10000"):
    return Fill(
        order_id="test-001",
        symbol="EURUSD",
        side=side,
        quantity=Decimal("1"),
        fill_price=Decimal(price),
    )


def _make_ctx(ts: datetime):
    return ExecutionContext(timestamp=ts, symbol="EURUSD")


class TestSpreadProfile:
    def test_default_forex_profile(self):
        profile = SpreadProfile.default_forex()
        assert TradingSession.LONDON in profile.session_spreads
        assert profile.session_spreads[TradingSession.LONDON].base_spread_pips == 0.8

    def test_off_hours_spread(self):
        profile = SpreadProfile.default_forex()
        # Max effective pips = Sydney 1.5; off_hours = 1.5 * 3.0 = 4.5
        assert profile.off_hours_pips == pytest.approx(4.5)


class TestVariableSpreadMiddleware:
    def test_london_buy_adds_half_spread(self):
        """During London, spread = 0.8 pips → half = 0.4 pips added to buy."""
        cal = ForexCalendar()
        mw = VariableSpreadMiddleware(calendar=cal)
        fill = _make_fill(Side.BUY, "1.10000")
        # Wednesday 10:00 UTC → London session
        ctx = _make_ctx(datetime(2024, 1, 10, 10, 0))

        result = mw.process_fill(fill, ctx, lambda f: f)

        expected = Decimal("1.10000") + Decimal("0.8") * Decimal("0.0001") / 2
        assert result.fill_price == expected

    def test_london_sell_subtracts_half_spread(self):
        cal = ForexCalendar()
        mw = VariableSpreadMiddleware(calendar=cal)
        fill = _make_fill(Side.SELL, "1.10000")
        ctx = _make_ctx(datetime(2024, 1, 10, 10, 0))

        result = mw.process_fill(fill, ctx, lambda f: f)

        expected = Decimal("1.10000") - Decimal("0.8") * Decimal("0.0001") / 2
        assert result.fill_price == expected

    def test_ny_session_spread(self):
        cal = ForexCalendar()
        mw = VariableSpreadMiddleware(calendar=cal)
        fill = _make_fill(Side.BUY, "1.10000")
        # Wednesday 18:00 UTC → NY only (London closed at 16:00)
        ctx = _make_ctx(datetime(2024, 1, 10, 18, 0))

        result = mw.process_fill(fill, ctx, lambda f: f)

        half_spread = Decimal("1.0") * Decimal("0.0001") / 2
        assert result.fill_price == Decimal("1.10000") + half_spread

    def test_overlap_london_ny_uses_tightest(self):
        """During London+NY overlap (12-16 UTC), use minimum spread (London 0.8)."""
        cal = ForexCalendar()
        mw = VariableSpreadMiddleware(calendar=cal)
        fill = _make_fill(Side.BUY, "1.10000")
        # Wednesday 14:00 UTC → London + NY overlap
        ctx = _make_ctx(datetime(2024, 1, 10, 14, 0))

        result = mw.process_fill(fill, ctx, lambda f: f)

        # Min of London(0.8) and NY(1.0) = 0.8
        half_spread = Decimal("0.8") * Decimal("0.0001") / 2
        assert result.fill_price == Decimal("1.10000") + half_spread

    def test_off_hours_wide_spread(self):
        """Off-hours (market open, no active session) → wide spread."""
        cal = ForexCalendar()
        mw = VariableSpreadMiddleware(calendar=cal)
        fill = _make_fill(Side.BUY, "1.10000")
        # Wednesday 21:30 UTC → after NY close (21:00), before Sydney (22:00)
        ctx = _make_ctx(datetime(2024, 1, 10, 21, 30))

        result = mw.process_fill(fill, ctx, lambda f: f)

        half_spread = Decimal("4.5") * Decimal("0.0001") / 2
        assert result.fill_price == Decimal("1.10000") + half_spread

    def test_custom_profile(self):
        """Custom spread profile with different values."""
        profile = SpreadProfile(
            session_spreads={
                TradingSession.LONDON: SessionSpreadConfig(base_spread_pips=0.5),
            },
            off_hours_multiplier=2.0,
        )
        cal = ForexCalendar()
        mw = VariableSpreadMiddleware(profile=profile, calendar=cal)
        fill = _make_fill(Side.BUY, "1.10000")
        ctx = _make_ctx(datetime(2024, 1, 10, 10, 0))  # London

        result = mw.process_fill(fill, ctx, lambda f: f)

        half_spread = Decimal("0.5") * Decimal("0.0001") / 2
        assert result.fill_price == Decimal("1.10000") + half_spread

    def test_metadata_stored_on_fill(self):
        cal = ForexCalendar()
        mw = VariableSpreadMiddleware(calendar=cal)
        fill = _make_fill(Side.BUY, "1.10000")
        ctx = _make_ctx(datetime(2024, 1, 10, 10, 0))

        result = mw.process_fill(fill, ctx, lambda f: f)

        assert "spread_pips" in result.metadata
        assert result.metadata["spread_pips"] == pytest.approx(0.8)
