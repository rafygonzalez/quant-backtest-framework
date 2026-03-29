from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from btframework.types import Timeframe


@dataclass(frozen=True)
class Instrument:
    """Represents a tradeable instrument."""
    symbol: str
    name: str = ""
    asset_class: str = "equity"  # equity, forex, crypto, futures
    exchange: str = ""
    currency: str = "USD"
    tick_size: Decimal = Decimal("0.01")
    lot_size: Decimal = Decimal("1")
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Bar:
    """OHLCV bar data."""
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: float = 0.0
    symbol: str = ""
    timeframe: str = "1d"

    def __post_init__(self):
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) < low ({self.low})")


@dataclass(frozen=True)
class Tick:
    """Tick-level data."""
    timestamp: datetime
    symbol: str
    price: Decimal
    volume: float = 0.0
    side: str = ""  # "buy", "sell", ""
