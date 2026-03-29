"""Tests for intra-bar price path generation and IntraBarExchange."""
import pytest
from datetime import datetime
from decimal import Decimal
from btframework.data.models import Bar
from btframework.execution.price_path import OHLCPricePathGenerator
from btframework.execution.exchange_v2 import IntraBarExchange
from btframework.execution.orders import Order
from btframework.types import Side, OrderType, OrderStatus


def _make_bar(o, h, l, c, symbol="TEST"):
    return Bar(
        timestamp=datetime(2024, 1, 10, 10, 0),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=1000,
        symbol=symbol,
    )


class TestOHLCPricePathGenerator:
    def test_respects_ohlc_bounds(self):
        gen = OHLCPricePathGenerator(seed=42)
        bar = _make_bar(100, 105, 95, 102)
        path = gen.generate(bar, num_points=60)

        assert len(path) == 60
        # First tick should be open
        assert path[0].price == Decimal("100")
        # Last tick should be close
        assert path[-1].price == Decimal("102")
        # All ticks within [low, high]
        for tick in path:
            assert Decimal("95") <= tick.price <= Decimal("105")

    def test_high_and_low_appear(self):
        gen = OHLCPricePathGenerator(seed=42)
        bar = _make_bar(100, 110, 90, 105)
        path = gen.generate(bar, num_points=60)

        prices = [t.price for t in path]
        assert Decimal("110") in prices
        assert Decimal("90") in prices

    def test_deterministic_with_seed(self):
        gen1 = OHLCPricePathGenerator(seed=123)
        gen2 = OHLCPricePathGenerator(seed=123)
        bar = _make_bar(100, 105, 95, 102)

        path1 = gen1.generate(bar)
        path2 = gen2.generate(bar)

        assert [t.price for t in path1] == [t.price for t in path2]

    def test_different_seeds_differ(self):
        gen1 = OHLCPricePathGenerator(seed=1)
        gen2 = OHLCPricePathGenerator(seed=2)
        bar = _make_bar(100, 105, 95, 102)

        path1 = gen1.generate(bar)
        path2 = gen2.generate(bar)

        # Paths should differ (except possibly at anchors)
        prices1 = [t.price for t in path1]
        prices2 = [t.price for t in path2]
        assert prices1 != prices2

    def test_bullish_bar_low_first_bias(self):
        """Bullish bars should tend to have low before high (70% prob)."""
        bar = _make_bar(100, 110, 90, 108)  # bullish
        low_first_count = 0
        total = 100
        for i in range(total):
            gen = OHLCPricePathGenerator(seed=i)
            path = gen.generate(bar, num_points=60)
            prices = [t.price for t in path]
            low_idx = prices.index(Decimal("90"))
            high_idx = prices.index(Decimal("110"))
            if low_idx < high_idx:
                low_first_count += 1

        # Should be around 70% (allow margin)
        assert low_first_count > 55
        assert low_first_count < 85

    def test_minimum_points(self):
        gen = OHLCPricePathGenerator(seed=42)
        bar = _make_bar(100, 105, 95, 102)
        path = gen.generate(bar, num_points=2)
        assert len(path) >= 4  # Minimum forced to 4


class TestIntraBarExchange:
    def test_market_order_fills_at_open(self):
        gen = OHLCPricePathGenerator(seed=42)
        exchange = IntraBarExchange(path_generator=gen)
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                      order_type=OrderType.MARKET)
        exchange.submit_order(order)

        bar = _make_bar(100, 105, 95, 102)
        candidates = exchange.process_bar("TEST", bar)

        assert len(candidates) == 1
        _, fill = candidates[0]
        assert fill.fill_price == Decimal("100")  # open

    def test_limit_buy_fills_at_limit(self):
        gen = OHLCPricePathGenerator(seed=42)
        exchange = IntraBarExchange(path_generator=gen)
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                      order_type=OrderType.LIMIT, price=Decimal("97"))
        exchange.submit_order(order)

        bar = _make_bar(100, 105, 95, 102)  # low=95 triggers limit
        candidates = exchange.process_bar("TEST", bar)

        assert len(candidates) == 1
        _, fill = candidates[0]
        assert fill.fill_price == Decimal("97")

    def test_stop_buy_fills_at_tick_price(self):
        gen = OHLCPricePathGenerator(seed=42)
        exchange = IntraBarExchange(path_generator=gen)
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                      order_type=OrderType.STOP, stop_price=Decimal("103"))
        exchange.submit_order(order)

        bar = _make_bar(100, 105, 95, 102)
        candidates = exchange.process_bar("TEST", bar)

        assert len(candidates) == 1
        _, fill = candidates[0]
        # Stop fills at tick price (slippage), not stop_price
        assert fill.fill_price >= Decimal("103")

    def test_stop_and_limit_same_bar_order_determined_by_path(self):
        """When both stop and limit can trigger in the same bar,
        the intra-bar path determines which fires first."""
        gen = OHLCPricePathGenerator(seed=42)
        exchange = IntraBarExchange(path_generator=gen)

        # Stop buy at 104 and limit buy at 96
        stop_order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                           order_type=OrderType.STOP, stop_price=Decimal("104"))
        limit_order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                            order_type=OrderType.LIMIT, price=Decimal("96"))

        exchange.submit_order(stop_order)
        exchange.submit_order(limit_order)

        bar = _make_bar(100, 105, 95, 102)  # Both can trigger
        candidates = exchange.process_bar("TEST", bar)

        # Both should fill
        assert len(candidates) == 2
        fill_prices = [f.fill_price for _, f in candidates]
        # Verify the fills are at the right levels
        assert any(p >= Decimal("104") for p in fill_prices)  # stop
        assert any(p == Decimal("96") for p in fill_prices)    # limit

    def test_no_fill_if_price_not_reached(self):
        gen = OHLCPricePathGenerator(seed=42)
        exchange = IntraBarExchange(path_generator=gen)
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                      order_type=OrderType.LIMIT, price=Decimal("80"))
        exchange.submit_order(order)

        bar = _make_bar(100, 105, 95, 102)  # low=95, never reaches 80
        candidates = exchange.process_bar("TEST", bar)

        # Order stays pending (GTC)
        assert len(candidates) == 0
