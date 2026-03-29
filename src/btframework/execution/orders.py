from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from btframework.types import Side, OrderType, OrderStatus, TimeInForce, Price, Quantity


@dataclass
class Order:
    """Represents a trading order with full lifecycle tracking."""
    symbol: str
    side: Side
    quantity: Quantity
    order_type: OrderType = OrderType.MARKET
    price: Price | None = None          # Limit price
    stop_price: Price | None = None     # Stop trigger price
    time_in_force: TimeInForce = TimeInForce.GTC
    status: OrderStatus = OrderStatus.PENDING
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    filled_at: datetime | None = None
    filled_quantity: Quantity = Decimal("0")
    filled_price: Price | None = None
    commission: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    metadata: dict = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL)

    @property
    def remaining_quantity(self) -> Quantity:
        return self.quantity - self.filled_quantity

    def fill(self, price: Price, quantity: Quantity, timestamp: datetime) -> None:
        self.filled_quantity += quantity
        self.filled_price = price
        self.filled_at = timestamp
        if self.filled_quantity >= self.quantity:
            self.status = OrderStatus.FILLED
        else:
            self.status = OrderStatus.PARTIAL

    def cancel(self) -> None:
        if self.is_active:
            self.status = OrderStatus.CANCELLED

    def reject(self, reason: str = "") -> None:
        self.status = OrderStatus.REJECTED
        self.metadata["reject_reason"] = reason

    def expire(self) -> None:
        """Expire the order (e.g., due to TimeInForce policy)."""
        if self.is_active:
            self.status = OrderStatus.EXPIRED
