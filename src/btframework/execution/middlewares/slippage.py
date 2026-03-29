from __future__ import annotations
from decimal import Decimal
import random
from btframework.types import Side
from btframework.core.middleware import ExecutionContext


class SlippageMiddleware:
    """Simulates price slippage on fills."""

    def __init__(self, pct: float = 0.001, model: str = "fixed"):
        self._pct = Decimal(str(pct))
        self._model = model  # "fixed", "random", "volume_impact"

    def process_order(self, order, ctx: ExecutionContext, next_fn):
        return next_fn(order)

    def process_fill(self, fill, ctx: ExecutionContext, next_fn):
        if self._model == "fixed":
            slippage_amount = fill.fill_price * self._pct
        elif self._model == "random":
            slippage_amount = fill.fill_price * self._pct * Decimal(str(random.uniform(0, 1)))
        elif self._model == "volume_impact":
            volume = ctx.volume or 1.0
            impact = min(float(fill.quantity) / max(volume * 0.01, 1), 1.0)
            slippage_amount = fill.fill_price * self._pct * Decimal(str(impact))
        else:
            slippage_amount = Decimal("0")

        fill.slippage = slippage_amount
        if fill.side == Side.BUY:
            fill.fill_price = fill.fill_price + slippage_amount
        else:
            fill.fill_price = fill.fill_price - slippage_amount

        return next_fn(fill)
