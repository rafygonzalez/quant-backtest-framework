from __future__ import annotations
from dataclasses import dataclass, field
from btframework.types import Side, OrderType, OrderStatus, TimeInForce, Price, Quantity
from btframework.events.base import Event


@dataclass(frozen=True)
class OrderEvent(Event):
    """Emitted when an order is created/submitted."""
    event_type: str = field(default="OrderEvent", init=False)
    order_id: str = ""
    symbol: str = ""
    side: Side = Side.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: Quantity = Quantity("0")
    price: Price | None = None  # For limit/stop orders
    stop_price: Price | None = None  # For stop orders
    time_in_force: TimeInForce = TimeInForce.GTC
    status: OrderStatus = OrderStatus.PENDING
    strategy_id: str = ""
