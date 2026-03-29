from __future__ import annotations
import logging
import uuid
from decimal import Decimal
from btframework.types import Side, OrderType, SignalType
from btframework.events.signal import SignalEvent
from btframework.events.order import OrderEvent
from btframework.events.fill import FillEvent
from btframework.events.base import EventBus
from btframework.execution.orders import Order
from btframework.execution.exchange import SimulatedExchange
from btframework.execution.fill import Fill
from btframework.execution.sizing import PositionSizer, SizingContext
from btframework.core.middleware import MiddlewarePipeline, ExecutionContext
from btframework.account.account import Account
from btframework.data.models import Bar

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Converts signals to orders, runs through middleware, submits to exchange.

    Uses two-phase fill protocol: exchange returns candidates, middleware
    processes them, then exchange confirms.
    """

    def __init__(
        self,
        exchange: SimulatedExchange,
        pipeline: MiddlewarePipeline,
        event_bus: EventBus,
        account: Account | None = None,
        calendar=None,
        sizer: PositionSizer | None = None,
    ):
        self._exchange = exchange
        self._pipeline = pipeline
        self._event_bus = event_bus
        self._account = account
        self._calendar = calendar
        self._sizer = sizer

    def process_signal(self, signal: SignalEvent, ctx: ExecutionContext) -> Order | None:
        """Convert a signal into an order and run through middleware."""
        # Calendar check: reject if market is closed
        if self._calendar is not None and not self._calendar.is_market_open(ctx.timestamp):
            logger.info(f"Signal rejected: market closed at {ctx.timestamp}")
            return None

        order = self._signal_to_order(signal, ctx)
        if order is None:
            return None

        # Pre-order margin check
        if self._account is not None:
            price = ctx.current_price
            if not self._account.check_margin(order.symbol, order.quantity, price):
                logger.info(f"Order rejected: insufficient margin for {order.order_id}")
                order.reject("Insufficient margin")
                return None

        # Run through middleware pipeline
        processed = self._pipeline.execute_order(order, ctx)
        if processed is None:
            logger.info(f"Order rejected by middleware: {order.order_id}")
            order.reject("Rejected by middleware")
            return None

        # Submit to exchange
        self._exchange.submit_order(processed)

        # Publish order event
        self._event_bus.publish(OrderEvent(
            order_id=processed.order_id,
            symbol=processed.symbol,
            side=processed.side,
            order_type=processed.order_type,
            quantity=processed.quantity,
            price=processed.price,
            stop_price=processed.stop_price,
            time_in_force=processed.time_in_force,
            status=processed.status,
            strategy_id=processed.strategy_id,
        ))

        return processed

    def process_bar(self, symbol: str, bar: Bar, ctx: ExecutionContext) -> list[Fill]:
        """Process pending orders against new bar data using two-phase protocol."""
        candidates = self._exchange.process_bar(symbol, bar)
        processed_fills: list[Fill] = []

        for order, candidate_fill in candidates:
            # Run fill through middleware pipeline
            processed = self._pipeline.execute_fill(candidate_fill, ctx)
            if processed is None:
                continue

            # Confirm fill on the exchange (commits to order, re-queues if partial)
            self._exchange.confirm_fill(order, processed)
            processed_fills.append(processed)

            # Publish fill event
            self._event_bus.publish(FillEvent(
                order_id=processed.order_id,
                symbol=processed.symbol,
                side=processed.side,
                quantity=processed.quantity,
                fill_price=processed.fill_price,
                commission=processed.commission,
                slippage=processed.slippage,
                strategy_id=processed.strategy_id,
            ))

        return processed_fills

    def _signal_to_order(self, signal: SignalEvent, ctx: ExecutionContext | None = None) -> Order | None:
        """Convert a SignalEvent into an Order."""
        side_map = {
            SignalType.ENTRY_LONG: Side.BUY,
            SignalType.ENTRY_SHORT: Side.SELL,
            SignalType.EXIT_LONG: Side.SELL,
            SignalType.EXIT_SHORT: Side.BUY,
        }
        side = side_map.get(signal.signal_type)
        if side is None:
            return None

        quantity = signal.target_quantity

        # Use sizer if no explicit quantity and sizer is configured
        if quantity is None and self._sizer is not None and ctx is not None:
            sizing_ctx = SizingContext(
                account=self._account,
                symbol=signal.symbol,
                side=side,
                current_price=Decimal(str(ctx.current_price)),
                stop_loss_price=signal.metadata.get("stop_loss_price") if signal.metadata else None,
                metadata=dict(signal.metadata) if signal.metadata else {},
            )
            quantity = self._sizer.calculate_size(sizing_ctx)

        if quantity is None:
            quantity = Decimal("1")

        order = Order(
            symbol=signal.symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            strategy_id=signal.strategy_id,
        )

        if signal.target_price:
            order.order_type = OrderType.LIMIT
            order.price = signal.target_price

        return order
