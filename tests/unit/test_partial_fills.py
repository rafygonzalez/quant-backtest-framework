"""Tests for partial fill re-queue behavior."""
from decimal import Decimal
from datetime import datetime
from btframework.execution.exchange import SimulatedExchange
from btframework.execution.orders import Order
from btframework.execution.fill import Fill
from btframework.data.models import Bar
from btframework.types import Side, OrderType, OrderStatus


def make_bar(ts, symbol="TEST", open_=100, high=105, low=95, close=102):
    return Bar(
        timestamp=ts,
        open=Decimal(str(open_)), high=Decimal(str(high)),
        low=Decimal(str(low)), close=Decimal(str(close)),
        symbol=symbol,
    )


class TestPartialFills:
    def test_partial_fill_requeues_order(self):
        """After a partial fill, order is re-queued with remaining quantity."""
        ex = SimulatedExchange()
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("200"))
        ex.submit_order(order)

        bar = make_bar(datetime(2024, 1, 1))
        candidates = ex.process_bar("TEST", bar)
        assert len(candidates) == 1

        ord_, fill = candidates[0]
        # Simulate partial fill by creating a fill with less quantity
        partial_fill = Fill(
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=fill.side,
            quantity=Decimal("100"),  # Only 100 of 200
            fill_price=fill.fill_price,
            timestamp=fill.timestamp,
            strategy_id=fill.strategy_id,
        )
        ex.confirm_fill(ord_, partial_fill)

        assert ord_.status == OrderStatus.PARTIAL
        assert ord_.filled_quantity == Decimal("100")
        assert ord_.remaining_quantity == Decimal("100")
        # Order should be re-queued
        assert len(ex.pending_orders) == 1
        assert ex.pending_orders[0].order_id == ord_.order_id

    def test_multiple_partial_fills_accumulate(self):
        """Multiple partial fills accumulate filled_quantity."""
        ex = SimulatedExchange()
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("300"))
        ex.submit_order(order)

        # Bar 1: fill 100 of 300
        bar1 = make_bar(datetime(2024, 1, 1))
        candidates = ex.process_bar("TEST", bar1)
        assert len(candidates) == 1
        ord_, fill1 = candidates[0]
        partial1 = Fill(
            order_id=fill1.order_id, symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=fill1.fill_price,
            timestamp=fill1.timestamp,
        )
        ex.confirm_fill(ord_, partial1)
        assert ord_.filled_quantity == Decimal("100")
        assert ord_.status == OrderStatus.PARTIAL

        # Bar 2: fill another 100
        bar2 = make_bar(datetime(2024, 1, 2))
        candidates2 = ex.process_bar("TEST", bar2)
        assert len(candidates2) == 1
        ord2, fill2 = candidates2[0]
        assert ord2.order_id == order.order_id
        # The candidate fill should use remaining_quantity
        assert fill2.quantity == Decimal("200")  # remaining
        partial2 = Fill(
            order_id=fill2.order_id, symbol="TEST", side=Side.BUY,
            quantity=Decimal("100"), fill_price=fill2.fill_price,
            timestamp=fill2.timestamp,
        )
        ex.confirm_fill(ord2, partial2)
        assert ord2.filled_quantity == Decimal("200")
        assert ord2.status == OrderStatus.PARTIAL

        # Bar 3: fill final 100
        bar3 = make_bar(datetime(2024, 1, 3))
        candidates3 = ex.process_bar("TEST", bar3)
        assert len(candidates3) == 1
        ord3, fill3 = candidates3[0]
        assert fill3.quantity == Decimal("100")
        ex.confirm_fill(ord3, fill3)
        assert ord3.status == OrderStatus.FILLED
        assert ord3.filled_quantity == Decimal("300")
        # No longer in pending
        assert len(ex.pending_orders) == 0

    def test_order_stays_partial_until_complete(self):
        """Order status stays PARTIAL until fully filled."""
        ex = SimulatedExchange()
        order = Order(symbol="TEST", side=Side.BUY, quantity=Decimal("100"))
        ex.submit_order(order)

        bar = make_bar(datetime(2024, 1, 1))
        candidates = ex.process_bar("TEST", bar)
        ord_, fill = candidates[0]

        # Partial: 50 of 100
        partial = Fill(
            order_id=fill.order_id, symbol="TEST", side=Side.BUY,
            quantity=Decimal("50"), fill_price=fill.fill_price,
            timestamp=fill.timestamp,
        )
        ex.confirm_fill(ord_, partial)
        assert ord_.status == OrderStatus.PARTIAL
        assert ord_.is_active
