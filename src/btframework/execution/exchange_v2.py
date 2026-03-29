"""Intra-bar exchange — walks a synthetic price path to determine fill order.

Subclass of SimulatedExchange that overrides _try_fill to evaluate
orders tick-by-tick, resolving ambiguities when both stop and limit
triggers exist within the same bar.

Supports bid/ask separation: when half_spread > 0, BUY orders see
ask prices (mid + half_spread) and SELL orders see bid prices
(mid - half_spread).
"""
from __future__ import annotations

from decimal import Decimal
from btframework.types import Side, OrderType, Price
from btframework.execution.exchange import SimulatedExchange
from btframework.execution.orders import Order
from btframework.execution.fill import Fill
from btframework.execution.price_path import (
    PricePathGenerator,
    OHLCPricePathGenerator,
)
from btframework.data.models import Bar


class IntraBarExchange(SimulatedExchange):
    """Exchange that uses intra-bar price paths for realistic fill simulation.

    Instead of using bar OHLC directly, generates a synthetic tick path
    and walks it sequentially. This determines which of multiple triggers
    (stop, limit) fires first within a single bar.
    """

    def __init__(self, path_generator: PricePathGenerator | None = None):
        super().__init__()
        self._path_gen = path_generator or OHLCPricePathGenerator()

    def _try_fill(self, order: Order, bar: Bar) -> Fill | None:
        """Walk the price path tick-by-tick to find the first trigger."""
        path = self._path_gen.generate(bar)

        # CRITICAL: scope stop_triggered to this bar only.
        # Previous bars must NOT influence this evaluation.
        stop_triggered = False

        for tick in path:
            fill_price, stop_triggered = self._check_trigger(
                order, tick.price, stop_triggered, self._half_spread,
            )
            if fill_price is not None:
                return Fill(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.remaining_quantity,
                    fill_price=fill_price,
                    timestamp=bar.timestamp,
                    strategy_id=order.strategy_id,
                )
        return None

    @staticmethod
    def _check_trigger(
        order: Order,
        mid_price: Price,
        stop_triggered: bool,
        half_spread: Decimal = Decimal("0"),
    ) -> tuple[Price | None, bool]:
        """Check if a single tick price triggers the order.

        Returns (fill_price_or_None, stop_triggered_state).

        Bid/ask logic:
          BUY orders see the ASK = mid + half_spread
          SELL orders see the BID = mid - half_spread
        """
        ask = mid_price + half_spread
        bid = mid_price - half_spread

        if order.order_type == OrderType.MARKET:
            # BUY at ask, SELL at bid
            price = ask if order.side == Side.BUY else bid
            return price, stop_triggered

        elif order.order_type == OrderType.LIMIT:
            if order.price is None:
                return None, stop_triggered
            # LIMIT BUY: ask drops to limit → fill at limit
            if order.side == Side.BUY and ask <= order.price:
                return order.price, stop_triggered
            # LIMIT SELL: bid rises to limit → fill at limit
            elif order.side == Side.SELL and bid >= order.price:
                return order.price, stop_triggered

        elif order.order_type == OrderType.STOP:
            if order.stop_price is None:
                return None, stop_triggered
            # STOP BUY: ask rises to stop → fill at ask (slippage)
            if order.side == Side.BUY and ask >= order.stop_price:
                return ask, stop_triggered
            # STOP SELL: bid drops to stop → fill at bid (slippage)
            elif order.side == Side.SELL and bid <= order.stop_price:
                return bid, stop_triggered

        elif order.order_type == OrderType.STOP_LIMIT:
            if order.stop_price is None or order.price is None:
                return None, stop_triggered

            # Phase 1: check if stop triggers on this tick
            if not stop_triggered:
                if order.side == Side.BUY and ask >= order.stop_price:
                    stop_triggered = True
                elif order.side == Side.SELL and bid <= order.stop_price:
                    stop_triggered = True

                # If stop just triggered, check limit on the SAME tick
                if stop_triggered:
                    if order.side == Side.BUY and ask <= order.price:
                        return order.price, stop_triggered
                    elif order.side == Side.SELL and bid >= order.price:
                        return order.price, stop_triggered

            # Phase 2: stop was already triggered, check limit only
            if stop_triggered:
                if order.side == Side.BUY and ask <= order.price:
                    return order.price, stop_triggered
                elif order.side == Side.SELL and bid >= order.price:
                    return order.price, stop_triggered

        return None, stop_triggered
