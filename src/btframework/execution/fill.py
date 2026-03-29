from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from btframework.types import Side, Price, Quantity, Money


@dataclass
class Fill:
    """Represents an order fill (execution)."""
    order_id: str
    symbol: str
    side: Side
    quantity: Quantity
    fill_price: Price
    commission: Money = Decimal("0")
    slippage: Money = Decimal("0")
    fill_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strategy_id: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def total_cost(self) -> Decimal:
        """Total cost including commission and slippage."""
        base = self.fill_price * self.quantity
        return base + self.commission + self.slippage

    @property
    def net_price(self) -> Price:
        """Effective price after slippage."""
        if self.side == Side.BUY:
            return self.fill_price + self.slippage / self.quantity
        return self.fill_price - self.slippage / self.quantity
