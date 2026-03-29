from __future__ import annotations
from typing import Protocol, Iterator, runtime_checkable
from datetime import datetime
from decimal import Decimal
import pandas as pd
from btframework.data.models import Bar
from btframework.events.market import MarketDataEvent
from btframework.data.providers.base import DataProvider
from btframework.exceptions import DataError


@runtime_checkable
class DataFeed(Protocol):
    """Protocol for data feeds."""

    def __iter__(self) -> Iterator[dict[str, MarketDataEvent]]:
        """Yield dict mapping symbol -> MarketDataEvent for each bar."""
        ...

    @property
    def symbols(self) -> list[str]:
        ...


class HistoricalDataFeed:
    """Feeds historical data bar-by-bar from a provider."""

    def __init__(
        self,
        provider: DataProvider,
        symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ):
        self._provider = provider
        self._symbols = symbols
        self._start = start
        self._end = end
        self._timeframe = timeframe
        self._data: dict[str, pd.DataFrame] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        for symbol in self._symbols:
            df = self._provider.fetch(symbol, self._start, self._end, self._timeframe)
            if df.empty:
                raise DataError(f"No data for {symbol}")
            self._data[symbol] = df
        self._loaded = True

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    def __iter__(self) -> Iterator[dict[str, MarketDataEvent]]:
        self._load()

        # Merge all timestamps
        all_timestamps = set()
        for df in self._data.values():
            all_timestamps.update(df["timestamp"].tolist())
        all_timestamps = sorted(all_timestamps)

        # Index each dataframe by timestamp
        indexed: dict[str, dict] = {}
        for symbol, df in self._data.items():
            indexed[symbol] = {row["timestamp"]: row for _, row in df.iterrows()}

        for ts in all_timestamps:
            events: dict[str, MarketDataEvent] = {}
            for symbol in self._symbols:
                row = indexed[symbol].get(ts)
                if row is not None:
                    events[symbol] = MarketDataEvent(
                        symbol=symbol,
                        open=Decimal(str(row["open"])),
                        high=Decimal(str(row["high"])),
                        low=Decimal(str(row["low"])),
                        close=Decimal(str(row["close"])),
                        volume=float(row["volume"]),
                        bar_timestamp=ts if isinstance(ts, datetime) else ts.to_pydatetime(),
                    )
            if events:
                yield events


class SlicedFeed:
    """Subset of pre-materialized bars for walk-forward optimization."""

    def __init__(
        self,
        bars: list[dict[str, MarketDataEvent]],
        start_idx: int,
        end_idx: int,
        symbols: list[str] | None = None,
    ):
        self._bars = bars
        self._start = start_idx
        self._end = end_idx
        self._symbols = symbols or (
            list(bars[0].keys()) if bars else []
        )

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    def __iter__(self) -> Iterator[dict[str, MarketDataEvent]]:
        yield from self._bars[self._start:self._end]
