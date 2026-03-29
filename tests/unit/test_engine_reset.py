"""Tests for engine reset — run() x2 produces same result."""
from decimal import Decimal
from datetime import datetime
from btframework.core.engine import BacktestEngine
from btframework.events.market import MarketDataEvent
from btframework.events.signal import SignalEvent
from btframework.strategy.base import StrategyContext
from btframework.types import SignalType


class SimpleStrategy:
    """Deterministic strategy: buy on bar 3, sell on bar 6."""
    strategy_id = "simple_test"

    def __init__(self):
        self.context = StrategyContext()
        self._bar_count = 0

    def on_bar(self, event: MarketDataEvent) -> None:
        self._bar_count += 1
        if self._bar_count == 3:
            self.context.emit_signal(SignalEvent(
                symbol=event.symbol,
                signal_type=SignalType.ENTRY_LONG,
                target_quantity=Decimal("10"),
                strategy_id=self.strategy_id,
            ))
        elif self._bar_count == 6:
            self.context.emit_signal(SignalEvent(
                symbol=event.symbol,
                signal_type=SignalType.EXIT_LONG,
                target_quantity=Decimal("10"),
                strategy_id=self.strategy_id,
            ))

    def warmup_period(self) -> int:
        return 0


class SyntheticFeed:
    """Deterministic synthetic data feed."""

    def __init__(self, bars: int = 10):
        self._bars = bars

    @property
    def symbols(self):
        return ["TEST"]

    def __iter__(self):
        base_price = Decimal("100")
        for i in range(self._bars):
            price = base_price + Decimal(str(i))
            event = MarketDataEvent(
                symbol="TEST",
                open=price,
                high=price + Decimal("2"),
                low=price - Decimal("2"),
                close=price + Decimal("1"),
                volume=1000.0,
                bar_timestamp=datetime(2024, 1, 1 + i),
            )
            yield {"TEST": event}


class TestEngineReset:
    def test_run_twice_same_result(self):
        """Running engine.run() twice produces identical results."""
        engine = (
            BacktestEngine()
            .with_feed(SyntheticFeed(bars=10))
            .with_strategy(SimpleStrategy)
        )

        result1 = engine.run()
        result2 = engine.run()

        s1 = result1.summary()
        s2 = result2.summary()

        assert s1["total_trades"] == s2["total_trades"]
        assert s1["final_equity"] == s2["final_equity"]
        assert s1["total_pnl"] == s2["total_pnl"]
        assert len(result1.equity_curve) == len(result2.equity_curve)

    def test_ohlcv_cleared_between_runs(self):
        """OHLCV data is fresh on each run."""
        engine = (
            BacktestEngine()
            .with_feed(SyntheticFeed(bars=5))
            .with_strategy(SimpleStrategy)
        )

        result1 = engine.run()
        result2 = engine.run()

        assert len(result1.ohlcv_data["TEST"]) == 5
        assert len(result2.ohlcv_data["TEST"]) == 5

    def test_strategy_reinstantiated(self):
        """Strategy is re-created on each run() call."""
        engine = (
            BacktestEngine()
            .with_feed(SyntheticFeed(bars=10))
            .with_strategy(SimpleStrategy)
        )

        result1 = engine.run()
        trades1 = result1.trades

        result2 = engine.run()
        trades2 = result2.trades

        # Both runs should produce the same trades
        assert len(trades1) == len(trades2)
        for t1, t2 in zip(trades1, trades2):
            assert t1["pnl"] == t2["pnl"]
            assert t1["entry_price"] == t2["entry_price"]
            assert t1["exit_price"] == t2["exit_price"]
