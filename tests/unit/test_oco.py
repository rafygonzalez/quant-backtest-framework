"""Tests for OCO (One-Cancels-Other) order groups on SimulatedExchange."""
from decimal import Decimal
from datetime import datetime

from btframework.execution.exchange import SimulatedExchange
from btframework.execution.orders import Order
from btframework.data.models import Bar
from btframework.types import Side, OrderType, OrderStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exchange() -> SimulatedExchange:
    return SimulatedExchange()


def _make_bar(
    *,
    open: str,
    high: str,
    low: str,
    close: str,
    symbol: str = "TEST",
    ts: datetime | None = None,
) -> Bar:
    return Bar(
        timestamp=ts or datetime(2024, 1, 2),
        open=Decimal(open),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        symbol=symbol,
    )


def _limit_sell(price: str, oco_group: str, **kw) -> Order:
    """TP for a LONG position: LIMIT SELL at a higher price."""
    return Order(
        symbol=kw.get("symbol", "TEST"),
        side=Side.SELL,
        quantity=Decimal(kw.get("quantity", "100")),
        order_type=OrderType.LIMIT,
        price=Decimal(price),
        metadata={"_oco_group": oco_group},
        strategy_id=kw.get("strategy_id", "strat"),
    )


def _stop_sell(stop_price: str, oco_group: str, **kw) -> Order:
    """SL for a LONG position: STOP SELL at a lower price."""
    return Order(
        symbol=kw.get("symbol", "TEST"),
        side=Side.SELL,
        quantity=Decimal(kw.get("quantity", "100")),
        order_type=OrderType.STOP,
        stop_price=Decimal(stop_price),
        metadata={"_oco_group": oco_group},
        strategy_id=kw.get("strategy_id", "strat"),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOCO:
    """OCO (One-Cancels-Other) order group behaviour."""

    def test_oco_tp_fills_sl_cancelled(self, exchange):
        """When TP (LIMIT SELL) fills, the SL (STOP SELL) in the same group
        is cancelled."""
        tp = _limit_sell("110", oco_group="bracket1")
        sl = _stop_sell("90", oco_group="bracket1")

        exchange.submit_order(tp)
        exchange.submit_order(sl)

        # Bar where only TP triggers: high reaches 110, low stays above SL
        bar = _make_bar(open="105", high="112", low="103", close="111")
        candidates = exchange.process_bar("TEST", bar)

        # Only the TP should fill
        assert len(candidates) == 1
        filled_order, fill = candidates[0]
        assert filled_order.order_id == tp.order_id
        assert fill.fill_price == Decimal("110")

        # SL must be cancelled
        assert sl.status == OrderStatus.CANCELLED

    def test_oco_sl_fills_tp_cancelled(self, exchange):
        """When SL (STOP SELL) fills, the TP (LIMIT SELL) in the same group
        is cancelled."""
        tp = _limit_sell("110", oco_group="bracket1")
        sl = _stop_sell("95", oco_group="bracket1")

        exchange.submit_order(tp)
        exchange.submit_order(sl)

        # Bar where only SL triggers: low reaches 95, high stays below TP
        bar = _make_bar(open="100", high="102", low="93", close="94")
        candidates = exchange.process_bar("TEST", bar)

        assert len(candidates) == 1
        filled_order, fill = candidates[0]
        assert filled_order.order_id == sl.order_id
        assert fill.fill_price == Decimal("95")

        # TP must be cancelled
        assert tp.status == OrderStatus.CANCELLED

    def test_oco_both_trigger_bullish_bar_sl_wins(self, exchange):
        """On a bullish bar (close > open) where both TP and SL would trigger,
        the STOP order fills first (low comes first on bullish bars)."""
        tp = _limit_sell("110", oco_group="bracket1")
        sl = _stop_sell("95", oco_group="bracket1")

        exchange.submit_order(tp)
        exchange.submit_order(sl)

        # Bullish bar: close > open, wide range touching both levels
        bar = _make_bar(open="100", high="112", low="93", close="111")
        candidates = exchange.process_bar("TEST", bar)

        # SL (STOP) wins on bullish bar
        assert len(candidates) == 1
        filled_order, fill = candidates[0]
        assert filled_order.order_id == sl.order_id
        assert filled_order.order_type == OrderType.STOP
        assert fill.fill_price == Decimal("95")

        # TP is cancelled
        assert tp.status == OrderStatus.CANCELLED

    def test_oco_both_trigger_bearish_bar_tp_wins(self, exchange):
        """On a bearish bar (close < open) where both TP and SL would trigger,
        the LIMIT order fills first (high comes first on bearish bars)."""
        tp = _limit_sell("110", oco_group="bracket1")
        sl = _stop_sell("95", oco_group="bracket1")

        exchange.submit_order(tp)
        exchange.submit_order(sl)

        # Bearish bar: close < open, wide range touching both levels
        bar = _make_bar(open="108", high="112", low="93", close="94")
        candidates = exchange.process_bar("TEST", bar)

        # TP (LIMIT) wins on bearish bar
        assert len(candidates) == 1
        filled_order, fill = candidates[0]
        assert filled_order.order_id == tp.order_id
        assert filled_order.order_type == OrderType.LIMIT
        assert fill.fill_price == Decimal("110")

        # SL is cancelled
        assert sl.status == OrderStatus.CANCELLED

    def test_oco_no_interference_with_non_oco(self, exchange):
        """Non-OCO orders are unaffected when an OCO group member fills."""
        tp = _limit_sell("110", oco_group="bracket1")
        sl = _stop_sell("95", oco_group="bracket1")

        # Independent market order (no OCO group)
        independent = Order(
            symbol="TEST",
            side=Side.BUY,
            quantity=Decimal("50"),
            order_type=OrderType.MARKET,
            strategy_id="other",
        )

        exchange.submit_order(tp)
        exchange.submit_order(sl)
        exchange.submit_order(independent)

        # Bar triggers the TP and also processes the market order
        bar = _make_bar(open="105", high="112", low="103", close="111")
        candidates = exchange.process_bar("TEST", bar)

        filled_ids = {c[0].order_id for c in candidates}
        assert tp.order_id in filled_ids
        assert independent.order_id in filled_ids

        # SL cancelled by OCO
        assert sl.status == OrderStatus.CANCELLED
        # Independent order unaffected
        assert independent.status != OrderStatus.CANCELLED

    def test_oco_multiple_groups(self, exchange):
        """Two different OCO groups operate independently."""
        # Group A
        tp_a = _limit_sell("110", oco_group="groupA")
        sl_a = _stop_sell("95", oco_group="groupA")
        # Group B
        tp_b = _limit_sell("120", oco_group="groupB")
        sl_b = _stop_sell("85", oco_group="groupB")

        exchange.submit_order(tp_a)
        exchange.submit_order(sl_a)
        exchange.submit_order(tp_b)
        exchange.submit_order(sl_b)

        # Bar triggers group A TP (high=112) but NOT group B TP (needs 120)
        # and NOT group B SL (low=100, needs 85)
        bar = _make_bar(open="105", high="112", low="100", close="111")
        candidates = exchange.process_bar("TEST", bar)

        filled_ids = {c[0].order_id for c in candidates}

        # Group A: TP fills, SL cancelled
        assert tp_a.order_id in filled_ids
        assert sl_a.status == OrderStatus.CANCELLED

        # Group B: neither fills, both remain pending
        assert tp_b.order_id not in filled_ids
        assert sl_b.order_id not in filled_ids
        pending_ids = {o.order_id for o in exchange.pending_orders}
        assert tp_b.order_id in pending_ids
        assert sl_b.order_id in pending_ids

    def test_oco_cancelled_order_not_in_pending(self, exchange):
        """After OCO cancellation the cancelled order is removed from the
        pending orders list."""
        tp = _limit_sell("110", oco_group="bracket1")
        sl = _stop_sell("95", oco_group="bracket1")

        exchange.submit_order(tp)
        exchange.submit_order(sl)

        # Trigger only TP
        bar = _make_bar(open="105", high="112", low="103", close="111")
        exchange.process_bar("TEST", bar)

        pending_ids = {o.order_id for o in exchange.pending_orders}
        assert sl.order_id not in pending_ids
        assert tp.order_id not in pending_ids  # filled, also removed

    def test_non_oco_orders_preserve_order(self, exchange):
        """Non-OCO orders maintain their original relative ordering after the
        OCO sort is applied (stable sort, all get priority 0)."""
        orders = [
            Order(
                symbol="TEST",
                side=Side.SELL,
                quantity=Decimal("10"),
                order_type=OrderType.LIMIT,
                price=Decimal(str(110 + i)),
                strategy_id=f"s{i}",
            )
            for i in range(4)
        ]

        for o in orders:
            exchange.submit_order(o)

        # Bar that fills all of them (high well above all limits)
        bar = _make_bar(open="115", high="120", low="108", close="116")
        candidates = exchange.process_bar("TEST", bar)

        filled_ids = [c[0].order_id for c in candidates]
        original_ids = [o.order_id for o in orders]

        # All filled and order preserved
        assert len(filled_ids) == len(original_ids)
        assert filled_ids == original_ids
