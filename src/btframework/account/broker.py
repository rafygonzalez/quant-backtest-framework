from __future__ import annotations
from typing import Protocol, runtime_checkable
from decimal import Decimal
from dataclasses import dataclass, field
from btframework.account.lot import LotSpec, EQUITY_LOT, FOREX_LOT, CRYPTO_LOT
from btframework.data.models import Instrument


@runtime_checkable
class BrokerProfile(Protocol):
    """Protocol for broker emulation."""
    name: str
    margin_type: str    # "portfolio", "reg_t", "cross", "isolated"
    default_leverage: Decimal

    def lot_spec(self, instrument: Instrument) -> LotSpec: ...
    def margin_required(self, instrument: Instrument, qty: Decimal, price: Decimal) -> Decimal: ...
    def validate_order(self, order) -> tuple[bool, str]: ...


@dataclass
class DefaultBrokerProfile:
    """Simple broker profile for equities."""
    name: str = "default"
    margin_type: str = "reg_t"
    default_leverage: Decimal = Decimal("1")
    _lot_specs: dict[str, LotSpec] = field(default_factory=dict)

    def lot_spec(self, instrument: Instrument) -> LotSpec:
        if instrument.symbol in self._lot_specs:
            return self._lot_specs[instrument.symbol]
        # Default by asset class
        defaults = {
            "equity": EQUITY_LOT,
            "forex": FOREX_LOT,
            "crypto": CRYPTO_LOT,
        }
        return defaults.get(instrument.asset_class, EQUITY_LOT)

    def margin_required(self, instrument: Instrument, qty: Decimal, price: Decimal) -> Decimal:
        spec = self.lot_spec(instrument)
        notional = spec.notional_value(price, qty)
        if self.default_leverage > 0:
            return notional / self.default_leverage
        return notional

    def validate_order(self, order) -> tuple[bool, str]:
        return True, ""


@dataclass
class InteractiveBrokersProfile:
    """Emulates Interactive Brokers rules."""
    name: str = "interactive_brokers"
    margin_type: str = "reg_t"
    default_leverage: Decimal = Decimal("4")  # Intraday
    _lot_specs: dict[str, LotSpec] = field(default_factory=dict)

    def lot_spec(self, instrument: Instrument) -> LotSpec:
        if instrument.symbol in self._lot_specs:
            return self._lot_specs[instrument.symbol]
        return EQUITY_LOT

    def margin_required(self, instrument: Instrument, qty: Decimal, price: Decimal) -> Decimal:
        notional = price * qty
        # RegT: 25% maintenance margin
        return notional * Decimal("0.25")

    def validate_order(self, order) -> tuple[bool, str]:
        if order.quantity <= 0:
            return False, "Quantity must be positive"
        return True, ""


@dataclass
class BinanceProfile:
    """Emulates Binance Futures rules."""
    name: str = "binance"
    margin_type: str = "cross"
    default_leverage: Decimal = Decimal("10")
    _lot_specs: dict[str, LotSpec] = field(default_factory=dict)

    def lot_spec(self, instrument: Instrument) -> LotSpec:
        if instrument.symbol in self._lot_specs:
            return self._lot_specs[instrument.symbol]
        return CRYPTO_LOT

    def margin_required(self, instrument: Instrument, qty: Decimal, price: Decimal) -> Decimal:
        notional = price * qty
        return notional / self.default_leverage

    def validate_order(self, order) -> tuple[bool, str]:
        if order.quantity <= 0:
            return False, "Quantity must be positive"
        return True, ""


@dataclass
class ForexBrokerProfile:
    """Emulates a typical Forex broker."""
    name: str = "forex_broker"
    margin_type: str = "isolated"
    default_leverage: Decimal = Decimal("100")
    _lot_specs: dict[str, LotSpec] = field(default_factory=dict)

    def lot_spec(self, instrument: Instrument) -> LotSpec:
        if instrument.symbol in self._lot_specs:
            return self._lot_specs[instrument.symbol]
        return FOREX_LOT

    def margin_required(self, instrument: Instrument, qty: Decimal, price: Decimal) -> Decimal:
        spec = self.lot_spec(instrument)
        notional = spec.notional_value(price, qty)
        return notional / self.default_leverage

    def validate_order(self, order) -> tuple[bool, str]:
        if order.quantity <= 0:
            return False, "Quantity must be positive"
        return True, ""
