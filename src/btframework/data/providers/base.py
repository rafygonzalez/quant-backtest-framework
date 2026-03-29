from __future__ import annotations
from typing import Protocol, runtime_checkable
from datetime import datetime
import pandas as pd
from btframework.data.models import Instrument


@runtime_checkable
class DataProvider(Protocol):
    """Protocol for data providers (Yahoo, Binance, CSV, etc.)."""
    name: str

    def fetch(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV data as DataFrame with columns: timestamp, open, high, low, close, volume."""
        ...

    def available_symbols(self) -> list[str]:
        """Return list of available symbols."""
        ...
