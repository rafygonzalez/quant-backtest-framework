"""Tests for contract_size-aware PnL calculation."""
from decimal import Decimal
from datetime import datetime
from btframework.account.account import Account, Position
from btframework.account.broker import ForexBrokerProfile
from btframework.data.models import Instrument
from btframework.execution.fill import Fill
from btframework.types import Side, PositionSide


def forex_instrument(symbol="EURUSD"):
    return Instrument(symbol=symbol, asset_class="forex", currency="USD")


class TestPositionContractSize:
    def test_unrealized_pnl_uses_contract_size(self):
        """Position.update_price multiplies by contract_size."""
        pos = Position(
            symbol="EURUSD",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            avg_entry_price=Decimal("1.1000"),
            opened_at=datetime(2024, 1, 1),
            contract_size=Decimal("100000"),
        )
        # 50 pips up: (1.1050 - 1.1000) * 1 * 100000 = 500
        pos.update_price(Decimal("1.1050"))
        assert pos.unrealized_pnl == Decimal("500.0000")

    def test_unrealized_pnl_short_with_contract_size(self):
        """Short position PnL also uses contract_size."""
        pos = Position(
            symbol="EURUSD",
            side=PositionSide.SHORT,
            quantity=Decimal("2"),
            avg_entry_price=Decimal("1.1000"),
            opened_at=datetime(2024, 1, 1),
            contract_size=Decimal("100000"),
        )
        # 30 pips down: (1.1000 - 1.0970) * 2 * 100000 = 600
        pos.update_price(Decimal("1.0970"))
        assert pos.unrealized_pnl == Decimal("600.0000")

    def test_contract_size_defaults_to_one(self):
        """Without contract_size, PnL is price_diff * qty (backwards compat)."""
        pos = Position(
            symbol="TEST",
            side=PositionSide.LONG,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("50"),
            opened_at=datetime(2024, 1, 1),
        )
        pos.update_price(Decimal("55"))
        assert pos.unrealized_pnl == Decimal("500")


class TestAccountContractSize:
    def test_realized_pnl_uses_contract_size(self):
        """Closing a forex position uses contract_size for PnL."""
        broker = ForexBrokerProfile()
        account = Account(initial_balance=10_000, broker=broker)
        account.register_instrument(forex_instrument())

        # Buy 1 lot at 1.1000
        buy = Fill(
            order_id="1", symbol="EURUSD", side=Side.BUY,
            quantity=Decimal("1"), fill_price=Decimal("1.1000"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        # Sell 1 lot at 1.1050 (50 pips profit)
        sell = Fill(
            order_id="2", symbol="EURUSD", side=Side.SELL,
            quantity=Decimal("1"), fill_price=Decimal("1.1050"),
            timestamp=datetime(2024, 1, 2),
        )
        account.process_fill(sell)

        # PnL = (1.1050 - 1.1000) * 1 * 100000 = 500
        assert len(account.closed_trades) == 1
        assert account.closed_trades[0]["pnl"] == Decimal("500.0000")
        assert account.closed_trades[0]["contract_size"] == Decimal("100000")
        assert account.balance == Decimal("10500.0000")

    def test_equity_reflects_contract_size(self):
        """Equity includes unrealized PnL with contract_size."""
        broker = ForexBrokerProfile()
        account = Account(initial_balance=10_000, broker=broker)
        account.register_instrument(forex_instrument())

        buy = Fill(
            order_id="1", symbol="EURUSD", side=Side.BUY,
            quantity=Decimal("1"), fill_price=Decimal("1.1000"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        # Price moves to 1.1020 (20 pips)
        account.update_market_prices(
            {"EURUSD": Decimal("1.1020")},
            datetime(2024, 1, 2),
        )
        # unrealized = (1.1020 - 1.1000) * 1 * 100000 = 200
        assert account.equity == Decimal("10200.0000")

    def test_no_instrument_defaults_contract_size_one(self):
        """Without registered instrument, contract_size=1 (backwards compat)."""
        account = Account(initial_balance=100_000)

        buy = Fill(
            order_id="1", symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=Decimal("50"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        sell = Fill(
            order_id="2", symbol="TEST", side=Side.SELL,
            quantity=Decimal("100"), fill_price=Decimal("60"),
            timestamp=datetime(2024, 1, 2),
        )
        account.process_fill(sell)

        # PnL = (60-50) * 100 * 1 = 1000
        assert account.closed_trades[0]["pnl"] == Decimal("1000")

    def test_reversal_inherits_contract_size(self):
        """Position reversal creates new position with correct contract_size."""
        broker = ForexBrokerProfile()
        account = Account(initial_balance=10_000, broker=broker)
        account.register_instrument(forex_instrument())

        buy = Fill(
            order_id="1", symbol="EURUSD", side=Side.BUY,
            quantity=Decimal("1"), fill_price=Decimal("1.1000"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        # Sell 2 lots → close 1 long, open 1 short
        sell = Fill(
            order_id="2", symbol="EURUSD", side=Side.SELL,
            quantity=Decimal("2"), fill_price=Decimal("1.1050"),
            timestamp=datetime(2024, 1, 2),
        )
        account.process_fill(sell)

        pos = account.open_positions[0]
        assert pos.side == PositionSide.SHORT
        assert pos.quantity == Decimal("1")
        assert pos.contract_size == Decimal("100000")
        assert pos.currency == "USD"

    def test_short_forex_pnl(self):
        """Short forex position with contract_size."""
        broker = ForexBrokerProfile()
        account = Account(initial_balance=10_000, broker=broker)
        account.register_instrument(forex_instrument())

        # Sell 1 lot at 1.1000
        sell = Fill(
            order_id="1", symbol="EURUSD", side=Side.SELL,
            quantity=Decimal("1"), fill_price=Decimal("1.1000"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(sell)

        # Buy back at 1.0950 (50 pips profit on short)
        buy = Fill(
            order_id="2", symbol="EURUSD", side=Side.BUY,
            quantity=Decimal("1"), fill_price=Decimal("1.0950"),
            timestamp=datetime(2024, 1, 2),
        )
        account.process_fill(buy)

        # PnL = (1.1000 - 1.0950) * 1 * 100000 = 500
        assert account.closed_trades[0]["pnl"] == Decimal("500.0000")
