"""Position sizing strategies.

Calculates order quantity based on account state, risk parameters,
and optionally volatility indicators.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from btframework.types import Side, Price, Quantity
from btframework.account.account import Account


@dataclass
class SizingContext:
    """Context passed to position sizers."""
    account: Account
    symbol: str
    side: Side
    current_price: Price
    stop_loss_price: Price | None = None
    indicators: Any = None
    metadata: dict = field(default_factory=dict)


class PositionSizer(Protocol):
    """Protocol for position sizing strategies."""

    def calculate_size(self, ctx: SizingContext) -> Quantity:
        ...


class FixedSizer:
    """Always returns a fixed quantity."""

    def __init__(self, quantity: Decimal = Decimal("1")):
        self._quantity = Decimal(str(quantity))

    def calculate_size(self, ctx: SizingContext) -> Quantity:
        return self._quantity


class RiskPercentSizer:
    """Size position so that a stop-loss hit loses at most risk_pct of equity.

    qty = (equity * risk_pct) / (|entry - SL| * contract_size)

    Requires stop_loss_price in SizingContext. Falls back to fixed quantity
    if stop_loss_price is not provided.
    """

    def __init__(
        self,
        risk_pct: float = 0.02,
        contract_size: Decimal = Decimal("100000"),
        max_size: Decimal | None = None,
        fallback_quantity: Decimal = Decimal("0.01"),
    ):
        self._risk_pct = Decimal(str(risk_pct))
        self._contract_size = contract_size
        self._max_size = max_size
        self._fallback = fallback_quantity

    def calculate_size(self, ctx: SizingContext) -> Quantity:
        if ctx.stop_loss_price is None:
            return self._fallback

        equity = ctx.account.equity
        risk_amount = equity * self._risk_pct
        distance = abs(ctx.current_price - ctx.stop_loss_price)

        if distance == 0:
            return self._fallback

        qty = risk_amount / (distance * self._contract_size)

        if self._max_size is not None:
            qty = min(qty, self._max_size)

        # Round down to 2 decimal places (standard lot sizing)
        qty = qty.quantize(Decimal("0.01"))
        return max(qty, Decimal("0.01"))


class VolatilitySizer:
    """Size position based on ATR (Average True Range).

    qty = (equity * risk_pct) / (ATR * multiplier * contract_size)

    Expects `indicators` in SizingContext to have an `atr` value,
    or `metadata["atr"]` to be set.
    """

    def __init__(
        self,
        risk_pct: float = 0.02,
        atr_multiplier: float = 2.0,
        contract_size: Decimal = Decimal("100000"),
        max_size: Decimal | None = None,
        fallback_quantity: Decimal = Decimal("0.01"),
    ):
        self._risk_pct = Decimal(str(risk_pct))
        self._atr_multiplier = Decimal(str(atr_multiplier))
        self._contract_size = contract_size
        self._max_size = max_size
        self._fallback = fallback_quantity

    def calculate_size(self, ctx: SizingContext) -> Quantity:
        atr = self._get_atr(ctx)
        if atr is None or atr <= 0:
            return self._fallback

        atr = Decimal(str(atr))
        equity = ctx.account.equity
        risk_amount = equity * self._risk_pct
        distance = atr * self._atr_multiplier

        if distance == 0:
            return self._fallback

        qty = risk_amount / (distance * self._contract_size)

        if self._max_size is not None:
            qty = min(qty, self._max_size)

        qty = qty.quantize(Decimal("0.01"))
        return max(qty, Decimal("0.01"))

    def _get_atr(self, ctx: SizingContext) -> float | None:
        """Extract ATR from context indicators or metadata."""
        # Try metadata first
        atr = ctx.metadata.get("atr")
        if atr is not None:
            return float(atr)
        # Try indicators
        if ctx.indicators is not None:
            if hasattr(ctx.indicators, "atr"):
                return float(ctx.indicators.atr)
            if hasattr(ctx.indicators, "get"):
                val = ctx.indicators.get("atr")
                if val is not None:
                    return float(val)
        return None
