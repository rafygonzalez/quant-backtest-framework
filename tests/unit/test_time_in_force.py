"""Tests for TimeInForce enforcement in SimulatedExchange."""
from decimal import Decimal
from datetime import datetime
from btframework.execution.exchange import SimulatedExchange
from btframework.execution.orders import Order
from btframework.data.models import Bar
from btframework.types import Side, OrderType, OrderStatus, TimeInForce


def make_bar(ts, symbol="TEST", open_=100, high=105, low=95, close=102):
    return Bar(
        timestamp=ts,
        open=Decimal(str(open_)), high=Decimal(str(high)),
        low=Decimal(str(low)), close=Decimal(str(close)),
        symbol=symbol,
    )


class TestGTC:
    def test_gtc_persists_across_bars(self):
        """GTC order stays pending if not filled."""
        ex = SimulatedExchange()
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.LIMIT, price=Decimal("80"),
            time_in_force=TimeInForce.GTC,
        )
        ex.submit_order(order)

        # Bar 1: price never hits 80
        candidates = ex.process_bar("TEST", make_bar(datetime(2024, 1, 1)))
        assert len(candidates) == 0
        assert order.is_active

        # Bar 2: still pending
        candidates = ex.process_bar("TEST", make_bar(datetime(2024, 1, 2)))
        assert len(candidates) == 0
        assert order.is_active
        assert len(ex.pending_orders) == 1

    def test_gtc_fills_eventually(self):
        """GTC order fills when price condition is met."""
        ex = SimulatedExchange()
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.LIMIT, price=Decimal("94"),
            time_in_force=TimeInForce.GTC,
        )
        ex.submit_order(order)

        # Bar 1: low is 95, doesn't reach 94
        candidates = ex.process_bar("TEST", make_bar(datetime(2024, 1, 1), low=95))
        assert len(candidates) == 0

        # Bar 2: low is 90, fills
        candidates = ex.process_bar("TEST", make_bar(datetime(2024, 1, 2), low=90))
        assert len(candidates) == 1


class TestDAY:
    def test_day_expires_next_bar(self):
        """DAY order expires if not filled on submission bar."""
        ex = SimulatedExchange()
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.LIMIT, price=Decimal("80"),
            time_in_force=TimeInForce.DAY,
        )
        ex.submit_order(order)

        # Bar 1: submitted_bar=0, current_bar=1 → expire
        candidates = ex.process_bar("TEST", make_bar(datetime(2024, 1, 1)))
        assert len(candidates) == 0
        assert order.status == OrderStatus.EXPIRED

    def test_day_fills_same_bar(self):
        """DAY MARKET order fills immediately (same bar)."""
        ex = SimulatedExchange()
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )
        # Submit on bar 0, process on bar 1 → DAY expires because submitted_bar(0) < current(1)
        ex.submit_order(order)
        candidates = ex.process_bar("TEST", make_bar(datetime(2024, 1, 1)))
        # DAY expires before fill attempt since submitted_bar < current_bar_index
        assert order.status == OrderStatus.EXPIRED


class TestIOC:
    def test_ioc_cancels_if_no_fill(self):
        """IOC order is expired if it can't fill."""
        ex = SimulatedExchange()
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.LIMIT, price=Decimal("80"),
            time_in_force=TimeInForce.IOC,
        )
        ex.submit_order(order)

        candidates = ex.process_bar("TEST", make_bar(datetime(2024, 1, 1)))
        assert len(candidates) == 0
        assert order.status == OrderStatus.EXPIRED

    def test_ioc_fills_if_price_met(self):
        """IOC order fills if price condition is met on first bar."""
        ex = SimulatedExchange()
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.IOC,
        )
        ex.submit_order(order)

        candidates = ex.process_bar("TEST", make_bar(datetime(2024, 1, 1)))
        assert len(candidates) == 1


class TestFOK:
    def test_fok_expires_if_no_fill(self):
        """FOK order expires if it can't be fully filled."""
        ex = SimulatedExchange()
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.LIMIT, price=Decimal("80"),
            time_in_force=TimeInForce.FOK,
        )
        ex.submit_order(order)

        candidates = ex.process_bar("TEST", make_bar(datetime(2024, 1, 1)))
        assert len(candidates) == 0
        assert order.status == OrderStatus.EXPIRED

    def test_fok_fills_if_complete(self):
        """FOK fills if full quantity can be filled."""
        ex = SimulatedExchange()
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.FOK,
        )
        ex.submit_order(order)

        candidates = ex.process_bar("TEST", make_bar(datetime(2024, 1, 1)))
        assert len(candidates) == 1
        _, fill = candidates[0]
        assert fill.quantity == Decimal("100")
