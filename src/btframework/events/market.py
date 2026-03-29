from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from btframework.types import Price
from btframework.events.base import Event


@dataclass(frozen=True)
class MarketDataEvent(Event):
    """Emitted when new market data (bar) arrives."""
    event_type: str = field(default="MarketDataEvent", init=False)
    symbol: str = ""
    open: Price | None = None
    high: Price | None = None
    low: Price | None = None
    close: Price | None = None
    volume: float = 0.0
    bar_timestamp: datetime | None = None
