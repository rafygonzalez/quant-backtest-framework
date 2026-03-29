"""Tests for walk-forward optimization."""
import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from btframework.data.feed import SlicedFeed
from btframework.events.market import MarketDataEvent
from btframework.optimization.walk_forward import (
    WalkForwardOptimizer, WalkForwardResult, WalkForwardWindow,
)
from btframework.core.engine import BacktestEngine, BacktestResult
from btframework.account.account import Account


def _make_bars(n, start_date=None, symbol="TEST"):
    """Create n synthetic bar events."""
    start = start_date or datetime(2024, 1, 1)
    bars = []
    for i in range(n):
        price = Decimal("100") + Decimal(str(i))
        event = MarketDataEvent(
            symbol=symbol,
            open=price,
            high=price + Decimal("2"),
            low=price - Decimal("2"),
            close=price + Decimal("1"),
            volume=1000.0,
            bar_timestamp=start + timedelta(days=i),
        )
        bars.append({symbol: event})
    return bars


class TestSlicedFeed:
    def test_yields_correct_slice(self):
        bars = _make_bars(20)
        feed = SlicedFeed(bars, 5, 10, ["TEST"])

        result = list(feed)
        assert len(result) == 5

    def test_symbols_property(self):
        bars = _make_bars(10)
        feed = SlicedFeed(bars, 0, 5, ["TEST"])
        assert feed.symbols == ["TEST"]

    def test_auto_detect_symbols(self):
        bars = _make_bars(10)
        feed = SlicedFeed(bars, 0, 5)
        assert "TEST" in feed.symbols

    def test_empty_slice(self):
        bars = _make_bars(10)
        feed = SlicedFeed(bars, 5, 5, ["TEST"])
        assert list(feed) == []


class _DummyStrategy:
    """Simple strategy that does nothing — for testing optimizer plumbing."""
    strategy_id = "dummy"

    def __init__(self, threshold=50, **kwargs):
        self.threshold = threshold

    def on_bar(self, event):
        pass

    def warmup_period(self):
        return 0


class TestWalkForwardWindow:
    def test_window_dataclass(self):
        w = WalkForwardWindow(
            window_index=0,
            is_start=datetime(2024, 1, 1),
            is_end=datetime(2024, 6, 1),
            oos_start=datetime(2024, 6, 1),
            oos_end=datetime(2024, 9, 1),
            is_metric=0.15,
            oos_metric=0.08,
        )
        assert w.window_index == 0
        assert w.is_metric == 0.15


class TestWalkForwardResult:
    def test_not_overfit(self):
        result = WalkForwardResult(
            windows=[],
            aggregate_oos_metric=0.10,
            overfitting_score=0.7,
        )
        assert result.is_overfit is False

    def test_is_overfit(self):
        result = WalkForwardResult(
            windows=[],
            aggregate_oos_metric=0.02,
            overfitting_score=0.3,
        )
        assert result.is_overfit is True

    def test_summary(self):
        w = WalkForwardWindow(
            window_index=0,
            is_start=datetime(2024, 1, 1),
            is_end=datetime(2024, 6, 1),
            oos_start=datetime(2024, 6, 1),
            oos_end=datetime(2024, 9, 1),
            best_params={"fast": 10},
            is_metric=0.15,
            oos_metric=0.08,
        )
        result = WalkForwardResult(
            windows=[w],
            aggregate_oos_metric=0.08,
            overfitting_score=0.53,
        )
        summary = result.summary()
        assert summary["num_windows"] == 1
        assert summary["is_overfit"] is False


class TestWalkForwardOptimizer:
    def test_insufficient_bars_raises(self):
        bars = _make_bars(10)

        class _Feed:
            @property
            def symbols(self):
                return ["TEST"]

            def __iter__(self):
                yield from bars

        optimizer = WalkForwardOptimizer(
            engine_factory=lambda: BacktestEngine().with_account(Account(initial_balance=10000)),
            param_space={"threshold": [50]},
            strategy_cls=_DummyStrategy,
            objective=lambda r: r.summary()["return_pct"],
        )

        with pytest.raises(ValueError, match="Not enough bars"):
            optimizer.run(_Feed(), is_bars=100, oos_bars=50)

    def test_single_window_runs(self):
        """Run optimizer with exactly enough bars for 1 window."""
        bars = _make_bars(100)

        class _Feed:
            @property
            def symbols(self):
                return ["TEST"]

            def __iter__(self):
                yield from bars

        optimizer = WalkForwardOptimizer(
            engine_factory=lambda: BacktestEngine().with_account(Account(initial_balance=10000)),
            param_space={"threshold": [50, 60]},
            strategy_cls=_DummyStrategy,
            objective=lambda r: r.summary().get("return_pct", 0),
        )

        result = optimizer.run(_Feed(), is_bars=60, oos_bars=30, step_bars=30)

        assert len(result.windows) >= 1
        assert result.windows[0].best_params is not None

    def test_rolling_windows(self):
        """Multiple rolling windows with step < total - is - oos."""
        bars = _make_bars(200)

        class _Feed:
            @property
            def symbols(self):
                return ["TEST"]

            def __iter__(self):
                yield from bars

        optimizer = WalkForwardOptimizer(
            engine_factory=lambda: BacktestEngine().with_account(Account(initial_balance=10000)),
            param_space={"threshold": [50]},
            strategy_cls=_DummyStrategy,
            objective=lambda r: r.summary().get("return_pct", 0),
        )

        result = optimizer.run(_Feed(), is_bars=80, oos_bars=30, step_bars=30)

        # Should have multiple windows
        assert len(result.windows) >= 2

    def test_overfitting_score_computed(self):
        bars = _make_bars(150)

        class _Feed:
            @property
            def symbols(self):
                return ["TEST"]

            def __iter__(self):
                yield from bars

        optimizer = WalkForwardOptimizer(
            engine_factory=lambda: BacktestEngine().with_account(Account(initial_balance=10000)),
            param_space={"threshold": [50]},
            strategy_cls=_DummyStrategy,
            objective=lambda r: r.summary().get("return_pct", 0),
        )

        result = optimizer.run(_Feed(), is_bars=80, oos_bars=30, step_bars=30)

        # Overfitting score should be computed (may be 0 if no trades)
        assert isinstance(result.overfitting_score, float)
