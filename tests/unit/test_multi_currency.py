"""Tests for multi-currency accounting."""
from decimal import Decimal
from datetime import datetime
from btframework.account.account import Account, Position
from btframework.account.broker import ForexBrokerProfile
from btframework.data.models import Instrument
from btframework.execution.fill import Fill
from btframework.types import Side, PositionSide


def jpy_instrument():
    """EUR/JPY — PnL is in JPY, needs conversion to USD."""
    return Instrument(
        symbol="EURJPY",
        asset_class="forex",
        currency="JPY",
    )


def usd_instrument():
    """EUR/USD — PnL is in USD, no conversion needed."""
    return Instrument(
        symbol="EURUSD",
        asset_class="forex",
        currency="USD",
    )


class TestFxRateConversion:
    def test_position_update_with_fx_rate(self):
        """Position.update_price applies fx_rate to PnL."""
        pos = Position(
            symbol="EURJPY",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            avg_entry_price=Decimal("160.000"),
            opened_at=datetime(2024, 1, 1),
            contract_size=Decimal("100000"),
            currency="JPY",
        )
        # 100 pips up in JPY: (162.000 - 160.000) * 1 * 100000 = 200,000 JPY
        # At JPY/USD = 0.0067: 200,000 * 0.0067 = 1340 USD
        pos.update_price(Decimal("162.000"), fx_rate=Decimal("0.0067"))
        assert pos.unrealized_pnl == Decimal("1340.0000000")

    def test_fx_rate_default_is_one(self):
        """Without fx_rate, conversion is 1:1."""
        pos = Position(
            symbol="EURUSD",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            avg_entry_price=Decimal("1.1000"),
            opened_at=datetime(2024, 1, 1),
            contract_size=Decimal("100000"),
            currency="USD",
        )
        pos.update_price(Decimal("1.1050"))
        # PnL in USD, no conversion: (0.0050) * 1 * 100000 = 500
        assert pos.unrealized_pnl == Decimal("500.0000")


class TestAccountMultiCurrency:
    def test_set_fx_rates(self):
        """set_fx_rates stores rates for later use."""
        account = Account(initial_balance=10_000, currency="USD")
        account.set_fx_rates({"JPY": Decimal("0.0067"), "EUR": Decimal("1.10")})
        assert account._get_fx_rate("JPY") == Decimal("0.0067")
        assert account._get_fx_rate("EUR") == Decimal("1.10")
        assert account._get_fx_rate("USD") == Decimal("1")  # Same as account
        assert account._get_fx_rate("GBP") == Decimal("1")  # Unknown → 1

    def test_unrealized_pnl_converted_to_account_currency(self):
        """Unrealized PnL is converted using FX rate."""
        broker = ForexBrokerProfile()
        account = Account(initial_balance=10_000, currency="USD", broker=broker)
        account.register_instrument(jpy_instrument())
        account.set_fx_rates({"JPY": Decimal("0.0067")})

        buy = Fill(
            order_id="1", symbol="EURJPY", side=Side.BUY,
            quantity=Decimal("1"), fill_price=Decimal("160.000"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        # Price moves to 161.000 (100 pips)
        # Raw PnL = (161 - 160) * 1 * 100000 = 100,000 JPY
        # Converted = 100,000 * 0.0067 = 670 USD
        account.update_market_prices(
            {"EURJPY": Decimal("161.000")},
            datetime(2024, 1, 2),
        )
        assert account.equity == Decimal("10000") + Decimal("670.0000000")

    def test_realized_pnl_converted_on_close(self):
        """Realized PnL uses FX rate when closing position."""
        broker = ForexBrokerProfile()
        account = Account(initial_balance=10_000, currency="USD", broker=broker)
        account.register_instrument(jpy_instrument())
        account.set_fx_rates({"JPY": Decimal("0.0067")})

        buy = Fill(
            order_id="1", symbol="EURJPY", side=Side.BUY,
            quantity=Decimal("1"), fill_price=Decimal("160.000"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        sell = Fill(
            order_id="2", symbol="EURJPY", side=Side.SELL,
            quantity=Decimal("1"), fill_price=Decimal("162.000"),
            timestamp=datetime(2024, 1, 2),
        )
        account.process_fill(sell)

        # PnL = (162 - 160) * 1 * 100000 * 0.0067 = 1340 USD
        trade = account.closed_trades[0]
        assert trade["pnl"] == Decimal("1340.0000000")
        assert trade["currency"] == "JPY"
        assert trade["fx_rate"] == Decimal("0.0067")
        assert account.balance == Decimal("10000") + Decimal("1340.0000000")

    def test_fx_rates_updated_via_update_market_prices(self):
        """FX rates can be passed alongside market prices."""
        broker = ForexBrokerProfile()
        account = Account(initial_balance=10_000, currency="USD", broker=broker)
        account.register_instrument(jpy_instrument())

        buy = Fill(
            order_id="1", symbol="EURJPY", side=Side.BUY,
            quantity=Decimal("1"), fill_price=Decimal("160.000"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        # Pass FX rates alongside prices
        account.update_market_prices(
            {"EURJPY": Decimal("161.000")},
            datetime(2024, 1, 2),
            fx_rates={"JPY": Decimal("0.0070")},
        )
        # PnL = (161 - 160) * 1 * 100000 * 0.0070 = 700 USD
        assert account.equity == Decimal("10700.0000000")

    def test_usd_instrument_no_conversion_needed(self):
        """USD-denominated instrument doesn't need FX conversion."""
        broker = ForexBrokerProfile()
        account = Account(initial_balance=10_000, currency="USD", broker=broker)
        account.register_instrument(usd_instrument())

        buy = Fill(
            order_id="1", symbol="EURUSD", side=Side.BUY,
            quantity=Decimal("1"), fill_price=Decimal("1.1000"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        account.update_market_prices(
            {"EURUSD": Decimal("1.1050")},
            datetime(2024, 1, 2),
        )
        # PnL = 0.0050 * 1 * 100000 * 1 = 500 USD (no FX conversion)
        assert account.equity == Decimal("10500.0000")

    def test_mixed_currency_positions(self):
        """Multiple positions in different currencies."""
        broker = ForexBrokerProfile()
        account = Account(initial_balance=20_000, currency="USD", broker=broker)
        account.register_instrument(usd_instrument())
        account.register_instrument(jpy_instrument())
        account.set_fx_rates({"JPY": Decimal("0.0067")})

        # Buy 1 lot EURUSD
        buy1 = Fill(
            order_id="1", symbol="EURUSD", side=Side.BUY,
            quantity=Decimal("1"), fill_price=Decimal("1.1000"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy1)

        # Buy 1 lot EURJPY
        buy2 = Fill(
            order_id="2", symbol="EURJPY", side=Side.BUY,
            quantity=Decimal("1"), fill_price=Decimal("160.000"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy2)

        account.update_market_prices(
            {
                "EURUSD": Decimal("1.1050"),  # +50 pips = +500 USD
                "EURJPY": Decimal("161.000"),  # +100 pips = +100,000 JPY = +670 USD
            },
            datetime(2024, 1, 2),
        )
        # Total unrealized = 500 + 670 = 1170
        assert account.equity == Decimal("20000") + Decimal("500.0000") + Decimal("670.0000000")

    def test_fx_rates_reset_with_account(self):
        """FX rates are cleared on account reset."""
        account = Account(initial_balance=10_000, currency="USD")
        account.set_fx_rates({"JPY": Decimal("0.0067")})
        account.reset()
        assert account._get_fx_rate("JPY") == Decimal("1")  # Cleared to default
