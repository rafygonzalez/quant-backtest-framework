"""End-to-end integration test with synthetic feed and deterministic trades."""
from decimal import Decimal
from datetime import datetime
from btframework.core.engine import BacktestEngine
from btframework.account.account import Account
from btframework.events.market import MarketDataEvent
from btframework.events.signal import SignalEvent
from btframework.strategy.base import StrategyContext
from btframework.types import SignalType


class DeterministicStrategy:
    """Buy on bar 5, sell on bar 10. Predictable entry/exit."""
    strategy_id = "deterministic"

    def __init__(self):
        self.context = StrategyContext()
        self._bar_count = 0

    def on_bar(self, event: MarketDataEvent) -> None:
        self._bar_count += 1
        if self._bar_count == 5:
            self.context.emit_signal(SignalEvent(
                symbol=event.symbol,
                signal_type=SignalType.ENTRY_LONG,
                target_quantity=Decimal("100"),
                strategy_id=self.strategy_id,
            ))
        elif self._bar_count == 10:
            self.context.emit_signal(SignalEvent(
                symbol=event.symbol,
                signal_type=SignalType.EXIT_LONG,
                target_quantity=Decimal("100"),
                strategy_id=self.strategy_id,
            ))

    def warmup_period(self) -> int:
        return 0


class SyntheticLinearFeed:
    """Feed where price increases linearly: 100, 101, 102, ..."""

    def __init__(self, bars: int = 15):
        self._bars = bars

    @property
    def symbols(self):
        return ["SYN"]

    def __iter__(self):
        for i in range(self._bars):
            price = Decimal("100") + Decimal(str(i))
            event = MarketDataEvent(
                symbol="SYN",
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=10000.0,
                bar_timestamp=datetime(2024, 1, 1 + i),
            )
            yield {"SYN": event}


class TestIntegrationE2E:
    def test_full_backtest_deterministic(self):
        """Complete backtest with known entry/exit and verifiable PnL."""
        account = Account(initial_balance=100_000)
        engine = (
            BacktestEngine()
            .with_feed(SyntheticLinearFeed(bars=15))
            .with_strategy(DeterministicStrategy)
            .with_account(account)
        )

        result = engine.run()
        summary = result.summary()

        # Strategy buys on bar 5 (signal emitted), fills on bar 6 at open
        # Bar 5: price=104, signal emitted at close
        # Bar 6: open=105 (entry fill)
        # Strategy sells on bar 10 (signal emitted), fills on bar 11 at open
        # Bar 10: price=109, signal emitted at close
        # Bar 11: open=110 (exit fill)
        # PnL = (110 - 105) * 100 = 500
        assert summary["total_trades"] == 1
        assert summary["total_pnl"] == 500.0

    def test_backtest_produces_equity_curve(self):
        """Equity curve is produced with correct number of points."""
        engine = (
            BacktestEngine()
            .with_feed(SyntheticLinearFeed(bars=15))
            .with_strategy(DeterministicStrategy)
        )

        result = engine.run()
        assert len(result.equity_curve) == 15

    def test_backtest_ohlcv_collected(self):
        """OHLCV data is collected for charting."""
        engine = (
            BacktestEngine()
            .with_feed(SyntheticLinearFeed(bars=15))
            .with_strategy(DeterministicStrategy)
        )

        result = engine.run()
        assert "SYN" in result.ohlcv_data
        assert len(result.ohlcv_data["SYN"]) == 15

    def test_run_twice_deterministic(self):
        """Running the same backtest twice yields identical results."""
        engine = (
            BacktestEngine()
            .with_feed(SyntheticLinearFeed(bars=15))
            .with_strategy(DeterministicStrategy)
        )

        r1 = engine.run()
        r2 = engine.run()

        assert r1.summary() == r2.summary()
        assert len(r1.trades) == len(r2.trades)
        for t1, t2 in zip(r1.trades, r2.trades):
            assert t1["pnl"] == t2["pnl"]
