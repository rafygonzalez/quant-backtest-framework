from __future__ import annotations
import logging
from datetime import datetime
from decimal import Decimal
from btframework.types import Side, OrderType, OrderStatus, TimeInForce, Price
from btframework.execution.orders import Order
from btframework.execution.fill import Fill
from btframework.data.models import Bar
from btframework.exceptions import ExchangeError

logger = logging.getLogger(__name__)


class SimulatedExchange:
    """Simulates order matching and execution.

    Orders submitted on bar N are executed on bar N+1 (no look-ahead bias).
    Uses a two-phase fill protocol: _try_fill returns a candidate Fill,
    confirm_fill commits it to the order.

    Supports bid/ask separation: set half_spread > 0 before process_bar
    so BUY orders fill at ask and SELL orders fill at bid.

    OCO (One-Cancels-Other) support:
      Orders with the same "_oco_group" metadata key are mutually exclusive.
      When one order in a group fills, others are automatically cancelled.
      Priority on same-bar conflicts uses bar direction heuristic:
        Bullish (C > O): STOP orders fill first (low comes first)
        Bearish (C < O): LIMIT orders fill first (high comes first)
    """

    def __init__(self):
        self._pending_orders: list[Order] = []
        self._fills: list[Fill] = []
        self._bar_index: int = 0
        self._half_spread: Decimal = Decimal("0")

    @property
    def half_spread(self) -> Decimal:
        return self._half_spread

    @half_spread.setter
    def half_spread(self, value: Decimal) -> None:
        self._half_spread = Decimal(str(value))

    def submit_order(self, order: Order) -> None:
        """Queue an order for execution on the next bar."""
        order.status = OrderStatus.SUBMITTED
        order.metadata["_submitted_bar"] = self._bar_index
        self._pending_orders.append(order)
        logger.debug(f"Order submitted: {order.order_id} {order.side.name} {order.quantity} {order.symbol}")

    def process_bar(self, symbol: str, bar: Bar) -> list[tuple[Order, Fill]]:
        """Process pending orders against the current bar.

        Returns list of (order, candidate_fill) tuples. Fills are NOT yet
        committed to orders — call confirm_fill() to finalize.
        """
        self._bar_index += 1
        candidates: list[tuple[Order, Fill]] = []
        remaining: list[Order] = []

        # Phase 0: Expire orders based on TimeInForce before attempting fills
        active_orders: list[Order] = []
        for order in self._pending_orders:
            if order.symbol != symbol:
                remaining.append(order)
                continue

            if not order.is_active:
                continue

            submitted_bar = order.metadata.get("_submitted_bar", 0)

            if order.time_in_force == TimeInForce.DAY:
                if submitted_bar < self._bar_index:
                    order.expire()
                    logger.debug(f"Order expired (DAY): {order.order_id}")
                    continue

            active_orders.append(order)

        # Phase 1: Try fills (with OCO group support)
        #
        # OCO (One-Cancels-Other): orders sharing the same "_oco_group"
        # metadata key are mutually exclusive. When one fills, others in
        # the group are cancelled.
        #
        # Priority within a bar uses direction heuristic:
        #   Bullish bar (C > O) → STOP orders first (low comes first)
        #   Bearish bar (C < O) → LIMIT orders first (high comes first)
        # This determines which bracket fills when both trigger on same bar.
        filled_oco: set[str] = set()

        is_bullish = bar.close > bar.open
        def _oco_priority(o: Order) -> int:
            if not o.metadata.get("_oco_group"):
                return 0  # non-OCO orders keep original order
            if is_bullish:
                # Low first → STOP fills first (SL for LONG, TP for SHORT)
                return 0 if o.order_type == OrderType.STOP else 1
            else:
                # High first → LIMIT fills first (TP for LONG, SL for SHORT)
                return 0 if o.order_type == OrderType.LIMIT else 1

        active_orders.sort(key=_oco_priority)

        for order in active_orders:
            submitted_bar = order.metadata.get("_submitted_bar", 0)

            # OCO: skip if another order in same group already filled
            oco_group = order.metadata.get("_oco_group")
            if oco_group and oco_group in filled_oco:
                order.cancel()
                logger.debug(f"Order cancelled (OCO): {order.order_id}")
                continue

            # FOK: must fill entire quantity — check before attempting
            if order.time_in_force == TimeInForce.FOK:
                candidate = self._try_fill(order, bar)
                if candidate is None or candidate.quantity < order.remaining_quantity:
                    order.expire()
                    logger.debug(f"Order expired (FOK, no full fill): {order.order_id}")
                    continue
                candidates.append((order, candidate))
                if oco_group:
                    filled_oco.add(oco_group)
                continue

            candidate = self._try_fill(order, bar)
            if candidate:
                candidates.append((order, candidate))
                if oco_group:
                    filled_oco.add(oco_group)
            else:
                # IOC: expire if not filled on submission bar
                if order.time_in_force == TimeInForce.IOC:
                    order.expire()
                    logger.debug(f"Order expired (IOC, no fill): {order.order_id}")
                else:
                    remaining.append(order)

        # Phase 2: Cancel unfilled OCO siblings of filled groups
        if filled_oco:
            survivors: list[Order] = []
            for order in remaining:
                oco = order.metadata.get("_oco_group")
                if oco and oco in filled_oco:
                    order.cancel()
                    logger.debug(f"Order cancelled (OCO sibling): {order.order_id}")
                else:
                    survivors.append(order)
            remaining = survivors

        self._pending_orders = remaining
        return candidates

    def confirm_fill(self, order: Order, fill: Fill) -> None:
        """Commit a candidate fill to the order. Re-queues if PARTIAL."""
        order.fill(fill.fill_price, fill.quantity, fill.timestamp)
        self._fills.append(fill)

        if order.status == OrderStatus.PARTIAL:
            # Re-queue for next bar with remaining quantity
            self._pending_orders.append(order)
            logger.debug(
                f"Partial fill re-queued: {order.order_id} "
                f"remaining={order.remaining_quantity}"
            )

    def _try_fill(self, order: Order, bar: Bar) -> Fill | None:
        """Attempt to fill an order against bar data.

        Returns a candidate Fill WITHOUT modifying the order.
        """
        fill_price = self._get_fill_price(order, bar, self._half_spread)
        if fill_price is None:
            return None

        return Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.remaining_quantity,
            fill_price=fill_price,
            timestamp=bar.timestamp,
            strategy_id=order.strategy_id,
        )

    @staticmethod
    def _get_fill_price(
        order: Order,
        bar: Bar,
        half_spread: Decimal = Decimal("0"),
    ) -> Price | None:
        """Determine fill price based on order type, bar data, and bid/ask spread.

        Bid/ask model:
          ask = mid + half_spread  (price to BUY)
          bid = mid - half_spread  (price to SELL)

        For OHLC bars, we derive bid/ask for each of O/H/L:
          ask_open = open + hs,   bid_open = open - hs
          ask_high = high + hs,   bid_high = high - hs
          ask_low  = low  + hs,   bid_low  = low  - hs
        """
        hs = half_spread

        if order.order_type == OrderType.MARKET:
            # BUY at ask_open, SELL at bid_open
            if order.side == Side.BUY:
                return bar.open + hs
            else:
                return bar.open - hs

        elif order.order_type == OrderType.LIMIT:
            if order.price is None:
                return None
            # LIMIT BUY: ask_low touches limit → fill at limit
            if order.side == Side.BUY and (bar.low + hs) <= order.price:
                return min(order.price, bar.open + hs)
            # LIMIT SELL: bid_high touches limit → fill at limit
            elif order.side == Side.SELL and (bar.high - hs) >= order.price:
                return max(order.price, bar.open - hs)

        elif order.order_type == OrderType.STOP:
            if order.stop_price is None:
                return None
            # STOP BUY: ask_high touches stop → fill at max(stop, ask_open)
            if order.side == Side.BUY and (bar.high + hs) >= order.stop_price:
                return max(order.stop_price, bar.open + hs)
            # STOP SELL: bid_low touches stop → fill at min(stop, bid_open)
            elif order.side == Side.SELL and (bar.low - hs) <= order.stop_price:
                return min(order.stop_price, bar.open - hs)

        elif order.order_type == OrderType.STOP_LIMIT:
            if order.stop_price is None or order.price is None:
                return None
            # Stop triggered?
            triggered = False
            if order.side == Side.BUY and (bar.high + hs) >= order.stop_price:
                triggered = True
            elif order.side == Side.SELL and (bar.low - hs) <= order.stop_price:
                triggered = True
            if triggered:
                # Then check limit on the same bar
                if order.side == Side.BUY and (bar.low + hs) <= order.price:
                    return min(order.price, bar.open + hs)
                elif order.side == Side.SELL and (bar.high - hs) >= order.price:
                    return max(order.price, bar.open - hs)

        return None

    @property
    def pending_orders(self) -> list[Order]:
        return list(self._pending_orders)

    @property
    def fill_history(self) -> list[Fill]:
        return list(self._fills)

    @property
    def bar_index(self) -> int:
        return self._bar_index

    def cancel_all(self, symbol: str | None = None) -> int:
        """Cancel all pending orders, optionally filtered by symbol."""
        count = 0
        for order in self._pending_orders:
            if symbol is None or order.symbol == symbol:
                order.cancel()
                count += 1
        self._pending_orders = [o for o in self._pending_orders if o.is_active]
        return count

    def reset(self) -> None:
        self._pending_orders.clear()
        self._fills.clear()
        self._bar_index = 0
        self._half_spread = Decimal("0")
