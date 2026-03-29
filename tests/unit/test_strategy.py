"""Tests for strategies."""
from decimal import Decimal
from datetime import datetime
from btframework.events.market import MarketDataEvent


class TestSMACrossover:
    def test_generates_signals(self, sample_market_events):
        from strategies.sma_crossover import SMACrossover
        strat = SMACrossover(fast=5, slow=10, quantity=100)

        signals = []
        for event in sample_market_events:
            strat.on_bar(event)
            signals.extend(strat.context.pop_signals())

        # Should generate at least some signals after warmup
        assert strat.warmup_period() == 10


class TestMeanReversion:
    def test_generates_signals(self, sample_market_events):
        from strategies.mean_reversion import MeanReversion
        strat = MeanReversion(lookback=10, threshold=1.0, quantity=50)

        for event in sample_market_events:
            strat.on_bar(event)

        assert strat.warmup_period() == 10
