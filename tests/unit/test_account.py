"""Tests for the account system."""
from decimal import Decimal
from datetime import datetime
from btframework.account.account import Account
from btframework.execution.fill import Fill
from btframework.types import Side


class TestAccount:
    def test_initial_state(self, account):
        assert account.balance == Decimal("100000")
        assert account.equity == Decimal("100000")
        assert len(account.open_positions) == 0

    def test_process_buy_fill(self, account):
        fill = Fill(
            order_id="1", symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=Decimal("50"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(fill)
        assert len(account.open_positions) == 1
        pos = account.open_positions[0]
        assert pos.symbol == "TEST"
        assert pos.quantity == Decimal("100")

    def test_close_position_with_profit(self, account):
        # Open
        buy = Fill(
            order_id="1", symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=Decimal("50"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        # Close
        sell = Fill(
            order_id="2", symbol="TEST", side=Side.SELL,
            quantity=Decimal("100"), fill_price=Decimal("60"),
            timestamp=datetime(2024, 1, 2),
        )
        account.process_fill(sell)

        assert len(account.open_positions) == 0
        assert len(account.closed_trades) == 1
        assert account.closed_trades[0]["pnl"] == Decimal("1000")  # (60-50)*100
        assert account.balance == Decimal("101000")

    def test_close_position_with_loss(self, account):
        buy = Fill(
            order_id="1", symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=Decimal("50"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        sell = Fill(
            order_id="2", symbol="TEST", side=Side.SELL,
            quantity=Decimal("100"), fill_price=Decimal("45"),
            timestamp=datetime(2024, 1, 2),
        )
        account.process_fill(sell)

        assert account.closed_trades[0]["pnl"] == Decimal("-500")
        assert account.balance == Decimal("99500")

    def test_commission_deducted(self, account):
        fill = Fill(
            order_id="1", symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=Decimal("50"),
            commission=Decimal("10"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(fill)
        assert account.balance == Decimal("99990")

    def test_equity_with_unrealized(self, account):
        fill = Fill(
            order_id="1", symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=Decimal("50"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(fill)
        account.update_market_prices(
            {"TEST": Decimal("55")},
            datetime(2024, 1, 2),
        )
        # Unrealized PnL = (55-50) * 100 = 500
        assert account.equity == Decimal("100500")

    def test_reset(self, account):
        fill = Fill(
            order_id="1", symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=Decimal("50"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(fill)
        account.reset()
        assert account.balance == Decimal("100000")
        assert len(account.open_positions) == 0
