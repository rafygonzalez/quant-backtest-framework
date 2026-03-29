from __future__ import annotations
from decimal import Decimal
from btframework.core.middleware import ExecutionContext


class CommissionMiddleware:
    """Applies commission/fees to fills."""

    def __init__(self, pct: float = 0.001, min_commission: float = 0.0, model: str = "percentage"):
        self._pct = Decimal(str(pct))
        self._min = Decimal(str(min_commission))
        self._model = model  # "percentage", "per_share", "tiered"
        self._per_share: Decimal = Decimal("0.005")  # For per_share model

    def process_order(self, order, ctx: ExecutionContext, next_fn):
        return next_fn(order)

    def process_fill(self, fill, ctx: ExecutionContext, next_fn):
        if self._model == "percentage":
            commission = fill.fill_price * fill.quantity * self._pct
        elif self._model == "per_share":
            commission = fill.quantity * self._per_share
        elif self._model == "tiered":
            # Simple tiered: lower rate for larger orders
            notional = fill.fill_price * fill.quantity
            if notional > Decimal("100000"):
                commission = notional * (self._pct * Decimal("0.5"))
            else:
                commission = notional * self._pct
        else:
            commission = Decimal("0")

        fill.commission = max(commission, self._min)
        return next_fn(fill)
