"""Tests for the 4 critical fixes:
  1. STOP_LIMIT metadata scoping (no cross-bar leak)
  2. Multi-position hedging mode
  3. Middleware error handling
  4. Bid/ask separation in exchanges
"""
import pytest
from datetime import datetime
from decimal import Decimal
from btframework.data.models import Bar
from btframework.execution.exchange import SimulatedExchange
from btframework.execution.exchange_v2 import IntraBarExchange
from btframework.execution.price_path import OHLCPricePathGenerator
from btframework.execution.orders import Order
from btframework.execution.fill import Fill
from btframework.account.account import Account, Position
from btframework.core.middleware import MiddlewarePipeline, ExecutionContext
from btframework.types import Side, OrderType, OrderStatus, PositionSide


def _bar(o, h, l, c, ts=None, symbol="TEST"):
    return Bar(
        timestamp=ts or datetime(2024, 1, 10),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)),
        volume=1000, symbol=symbol,
    )


# ============================================================
# FIX 1: STOP_LIMIT no cross-bar metadata leak
# ============================================================

class TestStopLimitNoCrossBarLeak:
    """The _stop_triggered flag must NOT persist between bars."""

    def test_stop_limit_resets_between_bars(self):
        """Bar 1: stop triggers but limit not met.
        Bar 2: price stays below stop — should NOT fill (stop must re-trigger).
        """
        gen = OHLCPricePathGenerator(seed=42)
        exchange = IntraBarExchange(path_generator=gen)

        # BUY STOP_LIMIT: stop=105, limit=103
        # Stop triggers at ask >= 105, then limit fills at ask <= 103
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
            order_type=OrderType.STOP_LIMIT,
            stop_price=Decimal("105"), price=Decimal("103"),
        )
        exchange.submit_order(order)

        # Bar 1: valid OHLC where high=106 triggers stop, but low=104 > 103 → no limit fill
        bar1 = _bar(104.5, 106, 104, 104.2, ts=datetime(2024, 1, 10))
        candidates = exchange.process_bar("TEST", bar1)
        assert len(candidates) == 0

        # Bar 2: price range 99-103 — NEVER reaches stop=105
        # If the old bug existed, _stop_triggered would persist and limit would fill at 103
        bar2 = _bar(101, 103, 99, 102, ts=datetime(2024, 1, 11))
        candidates = exchange.process_bar("TEST", bar2)
        # Must NOT fill — stop was not triggered on this bar
        assert len(candidates) == 0

    def test_stop_limit_fills_correctly_within_bar(self):
        """Stop and limit both trigger within the same bar.

        SELL STOP_LIMIT: stop triggers when price drops, then limit fills
        when price recovers above the limit.
        """
        gen = OHLCPricePathGenerator(seed=42)
        exchange = IntraBarExchange(path_generator=gen)

        # SELL STOP_LIMIT: stop=94, limit=97
        # Path will go: open=100 → high~100 → low=90 (stop triggers) → close=98 (limit fills)
        order = Order(
            symbol="TEST", side=Side.SELL, quantity=Decimal("1"),
            order_type=OrderType.STOP_LIMIT,
            stop_price=Decimal("94"), price=Decimal("97"),
        )
        exchange.submit_order(order)

        # Bar where price drops to 90 (stop at 94 triggers), then recovers to 98 (limit at 97 met)
        bar = _bar(100, 100, 90, 98, ts=datetime(2024, 1, 10))
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1
        _, fill = candidates[0]
        assert fill.fill_price == Decimal("97")

    def test_stop_limit_bar_exchange_also_works(self):
        """Verify SimulatedExchange STOP_LIMIT still works (it's stateless)."""
        exchange = SimulatedExchange()
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
            order_type=OrderType.STOP_LIMIT,
            stop_price=Decimal("105"), price=Decimal("103"),
        )
        exchange.submit_order(order)

        # Both trigger: high=110 (stop), low=100 (limit)
        bar = _bar(102, 110, 100, 108, ts=datetime(2024, 1, 10))
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1


# ============================================================
# FIX 2: Multi-position hedging mode
# ============================================================

class TestHedgingMode:
    """Account hedging mode: multiple positions per symbol via strategy_id."""

    def test_netting_mode_default(self):
        """Default netting mode — one position per symbol."""
        account = Account(initial_balance=10000)
        fill1 = Fill("o1", "EURUSD", Side.BUY, Decimal("1"), Decimal("1.1000"),
                      strategy_id="strat_a")
        fill2 = Fill("o2", "EURUSD", Side.BUY, Decimal("1"), Decimal("1.1100"),
                      strategy_id="strat_b")

        account.process_fill(fill1, datetime(2024, 1, 1))
        account.process_fill(fill2, datetime(2024, 1, 2))

        # Netting: one position with averaged entry
        assert len(account.open_positions) == 1
        pos = account.open_positions[0]
        assert pos.quantity == Decimal("2")
        assert pos.avg_entry_price == Decimal("1.1050")

    def test_hedging_mode_separate_positions(self):
        """Hedging mode — each strategy_id has its own position."""
        account = Account(initial_balance=10000, position_mode="hedging")
        fill1 = Fill("o1", "EURUSD", Side.BUY, Decimal("1"), Decimal("1.1000"),
                      strategy_id="strat_a")
        fill2 = Fill("o2", "EURUSD", Side.BUY, Decimal("1"), Decimal("1.1100"),
                      strategy_id="strat_b")

        account.process_fill(fill1, datetime(2024, 1, 1))
        account.process_fill(fill2, datetime(2024, 1, 2))

        assert len(account.open_positions) == 2
        pos_a = account.get_position("EURUSD", "strat_a")
        pos_b = account.get_position("EURUSD", "strat_b")
        assert pos_a is not None
        assert pos_b is not None
        assert pos_a.avg_entry_price == Decimal("1.1000")
        assert pos_b.avg_entry_price == Decimal("1.1100")

    def test_hedging_close_one_strategy(self):
        """Close only one strategy's position without affecting the other."""
        account = Account(initial_balance=10000, position_mode="hedging")
        fill_a = Fill("o1", "EURUSD", Side.BUY, Decimal("1"), Decimal("1.1000"),
                       strategy_id="strat_a")
        fill_b = Fill("o2", "EURUSD", Side.BUY, Decimal("1"), Decimal("1.1100"),
                       strategy_id="strat_b")
        close_a = Fill("o3", "EURUSD", Side.SELL, Decimal("1"), Decimal("1.1200"),
                        strategy_id="strat_a")

        account.process_fill(fill_a, datetime(2024, 1, 1))
        account.process_fill(fill_b, datetime(2024, 1, 2))
        account.process_fill(close_a, datetime(2024, 1, 3))

        assert len(account.open_positions) == 1
        remaining = account.open_positions[0]
        assert remaining.strategy_id == "strat_b"
        assert len(account.closed_trades) == 1
        assert float(account.closed_trades[0]["pnl"]) == pytest.approx(0.02, abs=0.001)

    def test_hedging_opposite_directions(self):
        """Two strategies can hold opposite positions on the same symbol."""
        account = Account(initial_balance=10000, position_mode="hedging")
        long_fill = Fill("o1", "EURUSD", Side.BUY, Decimal("1"), Decimal("1.1000"),
                          strategy_id="trend")
        short_fill = Fill("o2", "EURUSD", Side.SELL, Decimal("1"), Decimal("1.1000"),
                           strategy_id="mean_rev")

        account.process_fill(long_fill, datetime(2024, 1, 1))
        account.process_fill(short_fill, datetime(2024, 1, 1))

        assert len(account.open_positions) == 2
        positions = {p.strategy_id: p for p in account.open_positions}
        assert positions["trend"].side == PositionSide.LONG
        assert positions["mean_rev"].side == PositionSide.SHORT

    def test_get_positions_for_symbol(self):
        account = Account(initial_balance=10000, position_mode="hedging")
        fill1 = Fill("o1", "EURUSD", Side.BUY, Decimal("1"), Decimal("1.1000"),
                      strategy_id="s1")
        fill2 = Fill("o2", "GBPUSD", Side.BUY, Decimal("1"), Decimal("1.2700"),
                      strategy_id="s1")
        fill3 = Fill("o3", "EURUSD", Side.SELL, Decimal("1"), Decimal("1.1100"),
                      strategy_id="s2")

        account.process_fill(fill1, datetime(2024, 1, 1))
        account.process_fill(fill2, datetime(2024, 1, 1))
        account.process_fill(fill3, datetime(2024, 1, 1))

        eur_positions = account.get_positions_for_symbol("EURUSD")
        assert len(eur_positions) == 2
        gbp_positions = account.get_positions_for_symbol("GBPUSD")
        assert len(gbp_positions) == 1


# ============================================================
# FIX 3: Middleware error handling
# ============================================================

class _BrokenMiddleware:
    """Middleware that raises on process_fill."""
    def process_order(self, order, ctx, next_fn):
        return next_fn(order)

    def process_fill(self, fill, ctx, next_fn):
        raise RuntimeError("Simulated middleware failure")


class _BrokenOrderMiddleware:
    """Middleware that raises on process_order."""
    def process_order(self, order, ctx, next_fn):
        raise ValueError("Order middleware crash")

    def process_fill(self, fill, ctx, next_fn):
        return next_fn(fill)


class _CountingMiddleware:
    """Counts how many times it's called."""
    def __init__(self):
        self.order_count = 0
        self.fill_count = 0

    def process_order(self, order, ctx, next_fn):
        self.order_count += 1
        return next_fn(order)

    def process_fill(self, fill, ctx, next_fn):
        self.fill_count += 1
        return next_fn(fill)


class TestMiddlewareErrorHandling:
    def test_broken_fill_middleware_returns_none(self):
        """Broken middleware should return None (reject) instead of crashing."""
        pipeline = MiddlewarePipeline()
        pipeline.use(_BrokenMiddleware())

        ctx = ExecutionContext()
        fill = Fill("o1", "TEST", Side.BUY, Decimal("1"), Decimal("100"))

        result = pipeline.execute_fill(fill, ctx)
        assert result is None  # Rejected, not crashed

    def test_broken_order_middleware_returns_none(self):
        pipeline = MiddlewarePipeline()
        pipeline.use(_BrokenOrderMiddleware())

        ctx = ExecutionContext()
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"))

        result = pipeline.execute_order(order, ctx)
        assert result is None

    def test_broken_middleware_doesnt_affect_others(self):
        """Broken middleware in the chain should not prevent logging."""
        pipeline = MiddlewarePipeline()
        counter = _CountingMiddleware()
        pipeline.use(counter)
        pipeline.use(_BrokenMiddleware())

        ctx = ExecutionContext()
        fill = Fill("o1", "TEST", Side.BUY, Decimal("1"), Decimal("100"))

        result = pipeline.execute_fill(fill, ctx)
        # Counter was called, but broken MW caused rejection
        assert counter.fill_count == 1
        assert result is None

    def test_healthy_pipeline_still_works(self):
        """Normal pipeline should work exactly as before."""
        pipeline = MiddlewarePipeline()
        counter = _CountingMiddleware()
        pipeline.use(counter)

        ctx = ExecutionContext()
        fill = Fill("o1", "TEST", Side.BUY, Decimal("1"), Decimal("100"))

        result = pipeline.execute_fill(fill, ctx)
        assert result is not None
        assert result.fill_price == Decimal("100")
        assert counter.fill_count == 1


# ============================================================
# FIX 4: Bid/Ask separation in exchanges
# ============================================================

class TestBidAskSimulatedExchange:
    """Test bid/ask spread in SimulatedExchange."""

    def test_market_buy_fills_at_ask(self):
        exchange = SimulatedExchange()
        exchange.half_spread = Decimal("0.0005")  # 5 pips
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                      order_type=OrderType.MARKET)
        exchange.submit_order(order)

        bar = _bar(1.1000, 1.1050, 1.0950, 1.1020)
        candidates = exchange.process_bar("TEST", bar)

        assert len(candidates) == 1
        _, fill = candidates[0]
        # ask_open = 1.1000 + 0.0005 = 1.1005
        assert fill.fill_price == Decimal("1.1005")

    def test_market_sell_fills_at_bid(self):
        exchange = SimulatedExchange()
        exchange.half_spread = Decimal("0.0005")
        order = Order(symbol="TEST", side=Side.SELL, quantity=Decimal("1"),
                      order_type=OrderType.MARKET)
        exchange.submit_order(order)

        bar = _bar(1.1000, 1.1050, 1.0950, 1.1020)
        candidates = exchange.process_bar("TEST", bar)

        assert len(candidates) == 1
        _, fill = candidates[0]
        # bid_open = 1.1000 - 0.0005 = 1.0995
        assert fill.fill_price == Decimal("1.0995")

    def test_limit_buy_uses_ask_to_trigger(self):
        """LIMIT BUY triggers when ask_low <= limit_price."""
        exchange = SimulatedExchange()
        exchange.half_spread = Decimal("0.0005")
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                      order_type=OrderType.LIMIT, price=Decimal("1.0960"))
        exchange.submit_order(order)

        # bar.low = 1.0950, ask_low = 1.0950 + 0.0005 = 1.0955
        # 1.0955 <= 1.0960 → triggers
        bar = _bar(1.1000, 1.1050, 1.0950, 1.1020)
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1

    def test_limit_buy_blocked_by_spread(self):
        """LIMIT BUY at a price that mid-price reaches but ask doesn't."""
        exchange = SimulatedExchange()
        exchange.half_spread = Decimal("0.0010")  # 10 pips
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                      order_type=OrderType.LIMIT, price=Decimal("1.0955"))
        exchange.submit_order(order)

        # bar.low = 1.0950, ask_low = 1.0950 + 0.0010 = 1.0960
        # 1.0960 > 1.0955 → does NOT trigger (spread prevents it!)
        bar = _bar(1.1000, 1.1050, 1.0950, 1.1020)
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 0

    def test_stop_sell_uses_bid_to_trigger(self):
        """STOP SELL triggers when bid_low <= stop_price."""
        exchange = SimulatedExchange()
        exchange.half_spread = Decimal("0.0005")
        order = Order(symbol="TEST", side=Side.SELL, quantity=Decimal("1"),
                      order_type=OrderType.STOP, stop_price=Decimal("1.0940"))
        exchange.submit_order(order)

        # bar.low = 1.0950, bid_low = 1.0950 - 0.0005 = 1.0945
        # 1.0945 <= 1.0940 → NOT triggered (bid_low > stop)
        # Wait: 1.0945 > 1.0940? No, 1.0945 > 1.0940. So not triggered.
        # Actually: bid_low = 1.0945, stop = 1.0940 → 1.0945 > 1.0940 → NOT triggered
        bar = _bar(1.1000, 1.1050, 1.0950, 1.1020)
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 0

    def test_stop_sell_triggers_with_wider_move(self):
        """STOP SELL triggers when bid_low actually reaches stop."""
        exchange = SimulatedExchange()
        exchange.half_spread = Decimal("0.0005")
        order = Order(symbol="TEST", side=Side.SELL, quantity=Decimal("1"),
                      order_type=OrderType.STOP, stop_price=Decimal("1.0950"))
        exchange.submit_order(order)

        # bar.low = 1.0940, bid_low = 1.0940 - 0.0005 = 1.0935
        # 1.0935 <= 1.0950 → triggers!
        bar = _bar(1.1000, 1.1050, 1.0940, 1.1020)
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1
        _, fill = candidates[0]
        # Fill at min(stop_price, bid_open) = min(1.095, 1.0995) = 1.095
        assert fill.fill_price == Decimal("1.0950")

    def test_zero_spread_backwards_compatible(self):
        """With half_spread=0, behaves exactly like before."""
        exchange = SimulatedExchange()
        # half_spread defaults to 0
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                      order_type=OrderType.MARKET)
        exchange.submit_order(order)

        bar = _bar(1.1000, 1.1050, 1.0950, 1.1020)
        candidates = exchange.process_bar("TEST", bar)
        _, fill = candidates[0]
        assert fill.fill_price == Decimal("1.1000")  # open, as before


class TestBidAskIntraBarExchange:
    """Test bid/ask spread in IntraBarExchange."""

    def test_market_buy_at_ask(self):
        gen = OHLCPricePathGenerator(seed=42)
        exchange = IntraBarExchange(path_generator=gen)
        exchange.half_spread = Decimal("0.0005")

        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
                      order_type=OrderType.MARKET)
        exchange.submit_order(order)

        bar = _bar(1.1000, 1.1050, 1.0950, 1.1020)
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1
        _, fill = candidates[0]
        # First tick is open, ask = open + hs
        assert fill.fill_price > Decimal("1.1000")

    def test_market_sell_at_bid(self):
        gen = OHLCPricePathGenerator(seed=42)
        exchange = IntraBarExchange(path_generator=gen)
        exchange.half_spread = Decimal("0.0005")

        order = Order(symbol="TEST", side=Side.SELL, quantity=Decimal("1"),
                      order_type=OrderType.MARKET)
        exchange.submit_order(order)

        bar = _bar(1.1000, 1.1050, 1.0950, 1.1020)
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1
        _, fill = candidates[0]
        assert fill.fill_price < Decimal("1.1000")

    def test_stop_limit_intra_bar_with_spread(self):
        """STOP_LIMIT with spread: stop and limit check appropriate sides."""
        gen = OHLCPricePathGenerator(seed=42)
        exchange = IntraBarExchange(path_generator=gen)
        exchange.half_spread = Decimal("0.0005")

        # BUY STOP_LIMIT: stop=105, limit=106
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("1"),
            order_type=OrderType.STOP_LIMIT,
            stop_price=Decimal("105"), price=Decimal("106"),
        )
        exchange.submit_order(order)

        # High enough that ask hits stop, and ask stays below limit
        bar = _bar(100, 107, 98, 104)
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1
