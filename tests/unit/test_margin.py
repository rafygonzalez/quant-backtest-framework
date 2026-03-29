"""Tests for margin calculation, check, and enforcement."""
from decimal import Decimal
from datetime import datetime
from btframework.account.account import Account
from btframework.account.broker import ForexBrokerProfile, InteractiveBrokersProfile
from btframework.data.models import Instrument
from btframework.execution.fill import Fill
from btframework.types import Side


def equity_instrument(symbol="AAPL"):
    return Instrument(
        symbol=symbol,
        name="Apple Inc",
        asset_class="equity",
        exchange="NASDAQ",
        currency="USD",
    )


def make_fill(symbol="AAPL", side=Side.BUY, qty=100, price=100, **kwargs):
    return Fill(
        order_id="test-order",
        symbol=symbol,
        side=side,
        quantity=Decimal(str(qty)),
        fill_price=Decimal(str(price)),
        timestamp=datetime(2024, 1, 1),
        **kwargs,
    )


class TestMarginCalculation:
    def test_margin_calculated_with_instrument(self):
        """Margin is calculated when instruments are registered."""
        broker = InteractiveBrokersProfile()
        account = Account(initial_balance=100_000, broker=broker)
        inst = equity_instrument()
        account.register_instrument(inst)

        fill = make_fill(qty=100, price=100)
        account.process_fill(fill)

        # margin_required = price * qty * 0.25 = 100 * 100 * 0.25 = 2500
        account.update_market_prices(
            {"AAPL": Decimal("100")},
            datetime(2024, 1, 1),
        )
        assert account.margin_used == Decimal("2500")

    def test_margin_zero_without_instruments(self):
        """Without registered instruments, margin stays 0 (backwards compat)."""
        account = Account(initial_balance=100_000)
        fill = Fill(
            order_id="1", symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=Decimal("50"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(fill)
        account.update_market_prices(
            {"TEST": Decimal("50")},
            datetime(2024, 1, 1),
        )
        assert account.margin_used == Decimal("0")


class TestMarginCheck:
    def test_check_margin_passes_without_instrument(self):
        """check_margin returns True when no instrument registered."""
        account = Account(initial_balance=100)
        assert account.check_margin("UNKNOWN", Decimal("1000000"), Decimal("100")) is True

    def test_check_margin_passes_with_sufficient_margin(self):
        """check_margin returns True when enough free margin."""
        broker = InteractiveBrokersProfile()  # margin = notional * 0.25
        account = Account(initial_balance=100_000, broker=broker)
        inst = equity_instrument()
        account.register_instrument(inst)

        # 100 shares at $100 → margin = 2500
        assert account.check_margin("AAPL", Decimal("100"), Decimal("100")) is True

    def test_check_margin_fails_with_insufficient_margin(self):
        """check_margin returns False when not enough free margin."""
        broker = InteractiveBrokersProfile()
        account = Account(initial_balance=100, broker=broker)
        inst = equity_instrument()
        account.register_instrument(inst)

        # 100 shares at $100 → margin = 2500, but only have $100
        assert account.check_margin("AAPL", Decimal("100"), Decimal("100")) is False


class TestMarginCallAndLiquidation:
    def test_margin_call_emitted(self):
        """update_market_prices returns 'margin_call' when margin level drops below 100%."""
        broker = InteractiveBrokersProfile()  # margin = notional * 0.25
        account = Account(
            initial_balance=3_000,
            broker=broker,
            margin_call_level=100.0,
            maintenance_level=50.0,
        )
        inst = equity_instrument()
        account.register_instrument(inst)

        # Buy 100 shares at $100
        fill = make_fill(qty=100, price=100)
        account.process_fill(fill)

        # Price drops to $90: unrealized = (90-100)*100 = -1000
        # equity = 3000 - 1000 = 2000
        # margin = 90 * 100 * 0.25 = 2250
        # margin_level = 2000/2250*100 = 88.9% < 100%
        result = account.update_market_prices(
            {"AAPL": Decimal("90")},
            datetime(2024, 1, 2),
        )
        assert result == "margin_call"

    def test_liquidation_emitted(self):
        """update_market_prices returns 'liquidation' when below maintenance level."""
        broker = InteractiveBrokersProfile()
        account = Account(
            initial_balance=3_000,
            broker=broker,
            margin_call_level=100.0,
            maintenance_level=50.0,
        )
        inst = equity_instrument()
        account.register_instrument(inst)

        fill = make_fill(qty=100, price=100)
        account.process_fill(fill)

        # Price drops to $75: unrealized = (75-100)*100 = -2500
        # equity = 3000 - 2500 = 500
        # margin = 75 * 100 * 0.25 = 1875
        # margin_level = 500/1875*100 = 26.7% < 50%
        result = account.update_market_prices(
            {"AAPL": Decimal("75")},
            datetime(2024, 1, 2),
        )
        assert result == "liquidation"

    def test_no_margin_event_when_healthy(self):
        """No margin event when margin level is healthy."""
        broker = InteractiveBrokersProfile()
        account = Account(initial_balance=100_000, broker=broker)
        inst = equity_instrument()
        account.register_instrument(inst)

        fill = make_fill(qty=100, price=100)
        account.process_fill(fill)

        # equity = 100000, margin = 2500, level = 4000% >> 100%
        result = account.update_market_prices(
            {"AAPL": Decimal("100")},
            datetime(2024, 1, 1),
        )
        assert result is None
