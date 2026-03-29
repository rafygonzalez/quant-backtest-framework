from __future__ import annotations
from dataclasses import dataclass, field
from btframework.types import SignalType, Price, Quantity
from btframework.events.base import Event


@dataclass(frozen=True)
class SignalEvent(Event):
    """Emitted by a strategy when it generates a trading signal."""
    event_type: str = field(default="SignalEvent", init=False)
    symbol: str = ""
    signal_type: SignalType = SignalType.ENTRY_LONG
    strength: float = 1.0  # 0.0 to 1.0
    target_price: Price | None = None
    target_quantity: Quantity | None = None
    strategy_id: str = ""
    metadata: dict = field(default_factory=dict)
