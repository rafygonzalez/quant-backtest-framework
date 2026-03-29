"""Tests for indicators."""
from datetime import datetime
from decimal import Decimal
from btframework.strategy.indicators.examples import SMA
from btframework.strategy.indicators.base import PrecomputedIndicator, IndicatorPipeline
from btframework.data.models import Bar


def make_bar(price: float, i: int = 0) -> Bar:
    return Bar(
        timestamp=datetime(2024, 1, 1 + i),
        open=Decimal(str(price)),
        high=Decimal(str(price + 1)),
        low=Decimal(str(price - 1)),
        close=Decimal(str(price)),
        symbol="TEST",
    )


class TestSMA:
    def test_not_ready_before_period(self):
        sma = SMA(period=3)
        sma.update(make_bar(10, 0))
        sma.update(make_bar(20, 1))
        assert not sma.ready
        assert sma.value is None

    def test_ready_at_period(self):
        sma = SMA(period=3)
        for i, p in enumerate([10, 20, 30]):
            sma.update(make_bar(p, i))
        assert sma.ready
        assert sma.value == 20.0  # (10+20+30)/3

    def test_rolling_window(self):
        sma = SMA(period=3)
        for i, p in enumerate([10, 20, 30, 40]):
            sma.update(make_bar(p, i))
        assert sma.value == 30.0  # (20+30+40)/3


class TestPrecomputedIndicator:
    def test_basic_usage(self):
        timestamps = [datetime(2024, 1, i+1) for i in range(3)]
        values = [1.0, 2.0, 3.0]
        ind = PrecomputedIndicator("test", timestamps, values)

        for i in range(3):
            ind.update(make_bar(100, i))

        assert ind.ready
        assert ind.value == 3.0
        assert len(ind.series) == 3

    def test_reset(self):
        ind = PrecomputedIndicator("test", [datetime(2024, 1, 1)], [42.0])
        ind.update(make_bar(100, 0))
        ind.reset()
        assert not ind.ready


class TestIndicatorPipeline:
    def test_pipeline_aggregates(self):
        sma_fast = SMA(period=3)
        sma_slow = SMA(period=5)
        pipeline = IndicatorPipeline([sma_fast, sma_slow])

        for i in range(5):
            pipeline.update(make_bar(10 + i, i))

        assert pipeline["sma_3"].ready
        assert pipeline["sma_5"].ready
        assert pipeline.ready

    def test_warmup_period(self):
        pipeline = IndicatorPipeline([SMA(period=3), SMA(period=10)])
        assert pipeline.warmup_period == 10
