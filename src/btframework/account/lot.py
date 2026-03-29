from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LotSpec:
    """Defines lot/contract specifications for an instrument."""
    min_qty: Decimal = Decimal("1")          # Minimum order size
    step: Decimal = Decimal("1")             # Order size increment
    contract_size: Decimal = Decimal("1")    # Value per contract/lot (e.g., 100_000 for forex)
    tick_size: Decimal = Decimal("0.01")     # Minimum price movement
    tick_value: Decimal | None = None        # Monetary value per tick (for futures)

    def validate_quantity(self, qty: Decimal) -> tuple[bool, str]:
        """Validate that quantity meets lot specs."""
        if qty < self.min_qty:
            return False, f"Quantity {qty} below minimum {self.min_qty}"
        if self.step > 0:
            remainder = (qty - self.min_qty) % self.step
            if remainder != Decimal("0"):
                return False, f"Quantity {qty} not aligned to step {self.step}"
        return True, ""

    def round_quantity(self, qty: Decimal) -> Decimal:
        """Round quantity to nearest valid lot size."""
        if qty < self.min_qty:
            return self.min_qty
        if self.step > 0:
            steps = int((qty - self.min_qty) / self.step)
            return self.min_qty + self.step * steps
        return qty

    def notional_value(self, price: Decimal, qty: Decimal) -> Decimal:
        """Calculate notional value of a position."""
        return price * qty * self.contract_size

    def tick_pnl(self, ticks: int) -> Decimal:
        """PnL per contract for N ticks movement."""
        if self.tick_value:
            return self.tick_value * Decimal(str(ticks))
        return self.tick_size * Decimal(str(ticks)) * self.contract_size


# Common presets
EQUITY_LOT = LotSpec(min_qty=Decimal("1"), step=Decimal("1"), contract_size=Decimal("1"), tick_size=Decimal("0.01"))
FOREX_LOT = LotSpec(min_qty=Decimal("0.01"), step=Decimal("0.01"), contract_size=Decimal("100000"), tick_size=Decimal("0.00001"))
CRYPTO_LOT = LotSpec(min_qty=Decimal("0.001"), step=Decimal("0.001"), contract_size=Decimal("1"), tick_size=Decimal("0.01"))
