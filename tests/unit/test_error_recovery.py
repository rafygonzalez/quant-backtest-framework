"""Tests for error recovery in the engine main loop."""
from decimal import Decimal
from datetime import datetime
from btframework.core.engine import BacktestEngine
from btframework.core.config import BacktestConfig
from btframework.events.market import MarketDataEvent
from btframework.events.signal import SignalEvent
from btframework.strategy.base import StrategyContext
from btframework.types import SignalType


class CrashingStrategy:
    """Strategy that throws on bar 3."""
    strategy_id = "crasher"

    def __init__(self):
        self.context = StrategyContext()
        self._bar_count = 0

    def on_bar(self, event: MarketDataEvent) -> None:
        self._bar_count += 1
        if self._bar_count == 3:
            raise RuntimeError("Strategy exploded!")

    def warmup_period(self) -> int:
        return 0


class SyntheticFeed:
    """Deterministic synthetic data feed."""

    def __init__(self, bars: int = 5):
        self._bars = bars

    @property
    def symbols(self):
        return ["TEST"]

    def __iter__(self):
        for i in range(self._bars):
            price = Decimal("100") + Decimal(str(i))
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


class TestErrorRecovery:
    def test_strategy_exception_continues_in_normal_mode(self):
        """Strategy exception doesn't halt backtest in normal mode (fail_fast=False)."""
        config = BacktestConfig(fail_fast=False)
        engine = (
            BacktestEngine(config)
            .with_feed(SyntheticFeed(bars=5))
            .with_strategy(CrashingStrategy)
        )

        errors = []
        engine.hooks.on("on_strategy_error", lambda **kwargs: errors.append(kwargs["error"]))

        result = engine.run()

        # Backtest should complete despite error on bar 3
        assert result is not None
        assert len(errors) == 1
        assert "exploded" in str(errors[0])

    def test_fail_fast_raises_exception(self):
        """In fail_fast mode, strategy exception is re-raised."""
        config = BacktestConfig(fail_fast=True)
        engine = (
            BacktestEngine(config)
            .with_feed(SyntheticFeed(bars=5))
            .with_strategy(CrashingStrategy)
        )

        import pytest
        with pytest.raises(RuntimeError, match="Strategy exploded"):
            engine.run()

    def test_on_strategy_error_hook_receives_context(self):
        """on_strategy_error hook receives error, event, and bar_index."""
        config = BacktestConfig(fail_fast=False)
        engine = (
            BacktestEngine(config)
            .with_feed(SyntheticFeed(bars=5))
            .with_strategy(CrashingStrategy)
        )

        hook_data = []
        engine.hooks.on("on_strategy_error", lambda **kwargs: hook_data.append(kwargs))

        engine.run()

        assert len(hook_data) == 1
        assert "error" in hook_data[0]
        assert "event" in hook_data[0]
        assert "bar_index" in hook_data[0]
        assert hook_data[0]["bar_index"] == 3
