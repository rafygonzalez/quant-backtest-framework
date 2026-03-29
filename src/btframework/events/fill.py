from __future__ import annotations
from dataclasses import dataclass, field
from btframework.types import Side, Price, Quantity, Money
from btframework.events.base import Event


@dataclass(frozen=True)
class FillEvent(Event):
    """Emitted when an order is (partially) filled."""
    event_type: str = field(default="FillEvent", init=False)
    order_id: str = ""
    symbol: str = ""
    side: Side = Side.BUY
    quantity: Quantity = Quantity("0")
    fill_price: Price = Price("0")
    commission: Money = Money("0")
    slippage: Money = Money("0")
    strategy_id: str = ""
