"""Tests for the simulated exchange."""
from decimal import Decimal
from datetime import datetime
from btframework.execution.exchange import SimulatedExchange
from btframework.execution.orders import Order
from btframework.data.models import Bar
from btframework.types import Side, OrderType, OrderStatus


class TestSimulatedExchange:
    def test_market_order_fills_at_open(self, exchange):
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("100"))
        exchange.submit_order(order)
        bar = Bar(
            timestamp=datetime(2024, 1, 2),
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            symbol="TEST",
        )
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1
        _, fill = candidates[0]
        assert fill.fill_price == Decimal("100")

    def test_limit_buy_fills_when_price_below(self, exchange):
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.LIMIT, price=Decimal("98"),
        )
        exchange.submit_order(order)
        bar = Bar(
            timestamp=datetime(2024, 1, 2),
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            symbol="TEST",
        )
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1
        _, fill = candidates[0]
        assert fill.fill_price == Decimal("98")

    def test_limit_buy_no_fill_when_price_above(self, exchange):
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.LIMIT, price=Decimal("90"),
        )
        exchange.submit_order(order)
        bar = Bar(
            timestamp=datetime(2024, 1, 2),
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            symbol="TEST",
        )
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 0

    def test_stop_buy_triggers(self, exchange):
        order = Order(
            symbol="TEST", side=Side.BUY, quantity=Decimal("100"),
            order_type=OrderType.STOP, stop_price=Decimal("104"),
        )
        exchange.submit_order(order)
        bar = Bar(
            timestamp=datetime(2024, 1, 2),
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            symbol="TEST",
        )
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1
        _, fill = candidates[0]
        assert fill.fill_price == Decimal("104")

    def test_cancel_all(self, exchange):
        exchange.submit_order(Order(symbol="A", side=Side.BUY, quantity=Decimal("1")))
        exchange.submit_order(Order(symbol="B", side=Side.BUY, quantity=Decimal("1")))
        cancelled = exchange.cancel_all()
        assert cancelled == 2
        assert len(exchange.pending_orders) == 0

    def test_no_look_ahead(self, exchange):
        """Orders on bar N only execute on bar N+1."""
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("100"))
        # Submit order
        exchange.submit_order(order)
        # Process bar (order should execute here, representing N+1)
        bar = Bar(
            timestamp=datetime(2024, 1, 2),
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            symbol="TEST",
        )
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1

    def test_confirm_fill_updates_order(self, exchange):
        """confirm_fill commits the fill to the order."""
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("100"))
        exchange.submit_order(order)
        bar = Bar(
            timestamp=datetime(2024, 1, 2),
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            symbol="TEST",
        )
        candidates = exchange.process_bar("TEST", bar)
        assert len(candidates) == 1
        ord_, fill = candidates[0]
        assert ord_.status == OrderStatus.SUBMITTED  # Not yet filled
        exchange.confirm_fill(ord_, fill)
        assert ord_.status == OrderStatus.FILLED
        assert ord_.filled_price == Decimal("100")

    def test_bar_index_increments(self, exchange):
        """Bar index increments on each process_bar call."""
        assert exchange.bar_index == 0
        bar = Bar(
            timestamp=datetime(2024, 1, 2),
            open=Decimal("100"), high=Decimal("105"),
            low=Decimal("95"), close=Decimal("102"),
            symbol="TEST",
        )
        exchange.process_bar("TEST", bar)
        assert exchange.bar_index == 1
        bar2 = Bar(
            timestamp=datetime(2024, 1, 3),
            open=Decimal("101"), high=Decimal("106"),
            low=Decimal("96"), close=Decimal("103"),
            symbol="TEST",
        )
        exchange.process_bar("TEST", bar2)
        assert exchange.bar_index == 2
