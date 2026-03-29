"""Tests for position reversal logic."""
from decimal import Decimal
from datetime import datetime
from btframework.account.account import Account
from btframework.execution.fill import Fill
from btframework.types import Side, PositionSide


class TestPositionReversal:
    def test_long_to_short_reversal(self):
        """LONG 100, SELL 150 → close LONG 100 + open SHORT 50."""
        account = Account(initial_balance=100_000)

        # Open LONG 100 @ 50
        buy = Fill(
            order_id="1", symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=Decimal("50"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)
        assert len(account.open_positions) == 1
        assert account.open_positions[0].side == PositionSide.LONG

        # SELL 150 @ 60 → close LONG 100 (PnL +1000) + open SHORT 50
        sell = Fill(
            order_id="2", symbol="TEST", side=Side.SELL,
            quantity=Decimal("150"), fill_price=Decimal("60"),
            timestamp=datetime(2024, 1, 2),
        )
        account.process_fill(sell)

        assert len(account.closed_trades) == 1
        assert account.closed_trades[0]["pnl"] == Decimal("1000")
        assert account.closed_trades[0]["quantity"] == Decimal("100")

        assert len(account.open_positions) == 1
        pos = account.open_positions[0]
        assert pos.side == PositionSide.SHORT
        assert pos.quantity == Decimal("50")
        assert pos.avg_entry_price == Decimal("60")

    def test_short_to_long_reversal(self):
        """SHORT 100, BUY 150 → close SHORT 100 + open LONG 50."""
        account = Account(initial_balance=100_000)

        # Open SHORT 100 @ 60
        sell = Fill(
            order_id="1", symbol="TEST", side=Side.SELL,
            quantity=Decimal("100"), fill_price=Decimal("60"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(sell)
        assert account.open_positions[0].side == PositionSide.SHORT

        # BUY 150 @ 50 → close SHORT 100 (PnL +1000) + open LONG 50
        buy = Fill(
            order_id="2", symbol="TEST", side=Side.BUY,
            quantity=Decimal("150"), fill_price=Decimal("50"),
            timestamp=datetime(2024, 1, 2),
        )
        account.process_fill(buy)

        assert len(account.closed_trades) == 1
        assert account.closed_trades[0]["pnl"] == Decimal("1000")

        assert len(account.open_positions) == 1
        pos = account.open_positions[0]
        assert pos.side == PositionSide.LONG
        assert pos.quantity == Decimal("50")
        assert pos.avg_entry_price == Decimal("50")

    def test_partial_close_no_reversal(self):
        """LONG 100, SELL 50 → partial close, no reversal."""
        account = Account(initial_balance=100_000)

        buy = Fill(
            order_id="1", symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=Decimal("50"),
            timestamp=datetime(2024, 1, 1),
        )
        account.process_fill(buy)

        sell = Fill(
            order_id="2", symbol="TEST", side=Side.SELL,
            quantity=Decimal("50"), fill_price=Decimal("60"),
            timestamp=datetime(2024, 1, 2),
        )
        account.process_fill(sell)

        assert len(account.closed_trades) == 1
        assert account.closed_trades[0]["pnl"] == Decimal("500")
        assert len(account.open_positions) == 1
        pos = account.open_positions[0]
        assert pos.side == PositionSide.LONG
        assert pos.quantity == Decimal("50")

    def test_exact_close_no_reversal(self):
        """LONG 100, SELL 100 → exact close, no new position."""
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

        assert len(account.closed_trades) == 1
        assert len(account.open_positions) == 0
