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

    Orders submitted from a bar's close are executed on bar N+1 (no look-ahead
    bias). Orders submitted from a fill callback are live for the rest of the bar
    that produced the fill — see process_bar_late.
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

        return self._match(bar, active_orders, remaining)

    def process_bar_late(self, symbol: str, bar: Bar) -> list[tuple[Order, Fill]]:
        """Match orders submitted DURING this bar against the rest of it.

        A bracket is submitted from the strategy's fill callback — at the instant the
        parent order filled, not at the bar's close. Holding it back to the next bar
        leaves the position without a stop for the whole fill bar, so a bar that crosses
        the stop exits at the following open instead of at the stop. What the bar does
        after the fill is the order of events, not look-ahead.

        Only the part of the bar that FOLLOWS the parent's fill may be read, and for a
        mid-bar fill that is one side of the bar, not both — see _follows_parent_fill.
        An order held back here is not lost: it rests and is matched normally from the
        next bar, exactly as before.

        Does not advance the bar index: these orders belong to the bar being processed.
        """
        active_orders: list[Order] = []
        remaining: list[Order] = []
        for order in self._pending_orders:
            if (order.symbol == symbol and order.is_active
                    and order.metadata.get("_submitted_bar") == self._bar_index
                    and self._follows_parent_fill(order, bar)):
                active_orders.append(order)
            else:
                remaining.append(order)

        return self._match(bar, active_orders, remaining)

    def _follows_parent_fill(self, order: Order, bar: Bar) -> bool:
        """Is this order's trigger in the part of the bar that comes AFTER its parent?

        OHLC gives no path, but the parent's own fill price says which leg it sat on:

          filled at the open      the whole range is still ahead — both sides readable.
                                  The submitter says so with _parent_at_open, because
                                  the fill price alone cannot: cost is applied to fills
                                  by middleware AFTER the exchange prices them, so a
                                  market entry arrives here displaced from bar.open and
                                  would be mistaken for a mid-bar fill.
          filled BELOW the open   price came down to it, so the LOW is at or after the
                                  fill; the HIGH may already have happened
          filled ABOVE the open   price rose to it, so the HIGH is after and the LOW is
                                  the doubtful one

        So a trigger on the far side of the parent's fill is readable and one on the near
        side is not. Without this, a limit entry filling on a dip could collect a target
        set at a high the bar had already printed before the position existed — measured
        on AUD/USD as 7 trades and +$379 of pure hindsight.
        """
        if order.metadata.get("_parent_at_open"):
            return True
        parent = order.metadata.get("_parent_fill")
        if parent is None:
            return True
        if parent == bar.open:
            return True
        trigger = (order.stop_price
                   if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT)
                   else order.price)
        if trigger is None:
            return True
        return trigger <= parent if parent < bar.open else trigger >= parent

    def _match(
        self,
        bar: Bar,
        active_orders: list[Order],
        remaining: list[Order],
    ) -> list[tuple[Order, Fill]]:
        """Try fills for active_orders against bar; park the rest in remaining."""
        candidates: list[tuple[Order, Fill]] = []

        # Phase 1: Try fills (with OCO group support)
        #
        # OCO (One-Cancels-Other): orders sharing the same "_oco_group"
        # metadata key are mutually exclusive. When one fills, others in
        # the group are cancelled.
        #
        # Priority within a bar uses direction heuristic on the TRIGGER LEVEL:
        #   Bullish bar (C > O) → assume path O→L→H → below-market levels first
        #   Bearish bar (C < O) → assume path O→H→L → above-market levels first
        # A trigger is below-market for STOP SELL (long SL) and LIMIT BUY
        # (short TP); above-market for STOP BUY (short SL) and LIMIT SELL
        # (long TP). Type-only priority inverted the path for shorts.
        filled_oco: set[str] = set()

        is_bullish = bar.close > bar.open
        def _oco_priority(o: Order) -> int:
            if not o.metadata.get("_oco_group"):
                return 0  # non-OCO orders keep original order
            below = (o.side == Side.SELL) if o.order_type in (
                OrderType.STOP, OrderType.STOP_LIMIT) else (o.side == Side.BUY)
            return 0 if below == is_bullish else 1

        active_orders.sort(key=_oco_priority)

        for order in active_orders:
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
