"""Tests for position sizing strategies."""
import pytest
from decimal import Decimal
from btframework.execution.sizing import (
    FixedSizer, RiskPercentSizer, VolatilitySizer, SizingContext,
)
from btframework.account.account import Account
from btframework.types import Side


def _make_ctx(equity=10000, price="1.10000", sl_price=None, atr=None):
    account = Account(initial_balance=equity, currency="USD")
    metadata = {}
    if atr is not None:
        metadata["atr"] = atr
    return SizingContext(
        account=account,
        symbol="EURUSD",
        side=Side.BUY,
        current_price=Decimal(price),
        stop_loss_price=Decimal(str(sl_price)) if sl_price is not None else None,
        metadata=metadata,
    )


class TestFixedSizer:
    def test_returns_fixed_quantity(self):
        sizer = FixedSizer(quantity=Decimal("0.1"))
        ctx = _make_ctx()
        assert sizer.calculate_size(ctx) == Decimal("0.1")

    def test_default_quantity(self):
        sizer = FixedSizer()
        ctx = _make_ctx()
        assert sizer.calculate_size(ctx) == Decimal("1")


class TestRiskPercentSizer:
    def test_with_stop_loss(self):
        """2% risk on $10k equity, 50-pip SL, 100k contract → 0.04 lots."""
        sizer = RiskPercentSizer(
            risk_pct=0.02,
            contract_size=Decimal("100000"),
        )
        # 50-pip SL: 1.10000 - 1.09500 = 0.00500
        ctx = _make_ctx(equity=10000, price="1.10000", sl_price="1.09500")

        qty = sizer.calculate_size(ctx)

        # risk_amount = 10000 * 0.02 = 200
        # distance = 0.00500
        # qty = 200 / (0.005 * 100000) = 200 / 500 = 0.40
        assert qty == Decimal("0.40")

    def test_small_stop_loss_larger_position(self):
        """Tighter SL → larger position."""
        sizer = RiskPercentSizer(risk_pct=0.02, contract_size=Decimal("100000"))
        # 20-pip SL
        ctx = _make_ctx(equity=10000, price="1.10000", sl_price="1.09800")

        qty = sizer.calculate_size(ctx)

        # distance = 0.002, qty = 200 / (0.002 * 100000) = 200/200 = 1.00
        assert qty == Decimal("1.00")

    def test_max_size_cap(self):
        sizer = RiskPercentSizer(
            risk_pct=0.02,
            contract_size=Decimal("100000"),
            max_size=Decimal("0.10"),
        )
        ctx = _make_ctx(equity=10000, price="1.10000", sl_price="1.09800")

        qty = sizer.calculate_size(ctx)
        assert qty == Decimal("0.10")

    def test_no_stop_loss_returns_fallback(self):
        sizer = RiskPercentSizer(risk_pct=0.02, fallback_quantity=Decimal("0.01"))
        ctx = _make_ctx(equity=10000, price="1.10000", sl_price=None)

        qty = sizer.calculate_size(ctx)
        assert qty == Decimal("0.01")

    def test_zero_distance_returns_fallback(self):
        sizer = RiskPercentSizer(risk_pct=0.02, fallback_quantity=Decimal("0.01"))
        ctx = _make_ctx(equity=10000, price="1.10000", sl_price="1.10000")

        qty = sizer.calculate_size(ctx)
        assert qty == Decimal("0.01")

    def test_minimum_quantity(self):
        """Even with very large SL, minimum is 0.01."""
        sizer = RiskPercentSizer(risk_pct=0.01, contract_size=Decimal("100000"))
        # Huge 1000-pip SL
        ctx = _make_ctx(equity=100, price="1.10000", sl_price="1.00000")

        qty = sizer.calculate_size(ctx)
        assert qty >= Decimal("0.01")


class TestVolatilitySizer:
    def test_with_atr_in_metadata(self):
        """ATR-based sizing from metadata."""
        sizer = VolatilitySizer(
            risk_pct=0.02,
            atr_multiplier=2.0,
            contract_size=Decimal("100000"),
        )
        # ATR = 0.00500 (50 pips)
        ctx = _make_ctx(equity=10000, price="1.10000", atr=0.005)

        qty = sizer.calculate_size(ctx)

        # distance = 0.005 * 2.0 = 0.01
        # risk = 10000 * 0.02 = 200
        # qty = 200 / (0.01 * 100000) = 200/1000 = 0.20
        assert qty == Decimal("0.20")

    def test_no_atr_returns_fallback(self):
        sizer = VolatilitySizer(fallback_quantity=Decimal("0.01"))
        ctx = _make_ctx(equity=10000, price="1.10000")

        qty = sizer.calculate_size(ctx)
        assert qty == Decimal("0.01")

    def test_max_size_cap(self):
        sizer = VolatilitySizer(
            risk_pct=0.02,
            atr_multiplier=2.0,
            contract_size=Decimal("100000"),
            max_size=Decimal("0.05"),
        )
        ctx = _make_ctx(equity=10000, price="1.10000", atr=0.005)

        qty = sizer.calculate_size(ctx)
        assert qty == Decimal("0.05")
