from __future__ import annotations
from typing import Protocol, runtime_checkable
from datetime import datetime
from btframework.data.models import Bar


@runtime_checkable
class Indicator(Protocol):
    """Protocol for indicators - custom or precomputed."""
    name: str

    def update(self, bar: Bar) -> None:
        """Feed a new bar to the indicator."""
        ...

    @property
    def value(self) -> float | tuple[float, ...] | None:
        """Current indicator value."""
        ...

    @property
    def ready(self) -> bool:
        """True when enough data has been consumed."""
        ...

    def reset(self) -> None:
        """Reset internal state."""
        ...

    @property
    def period(self) -> int:
        """Warmup period needed."""
        ...

    @property
    def series(self) -> list[float | None]:
        """Full history of values."""
        ...


class PrecomputedIndicator:
    """Indicator with pre-calculated values (e.g., from an API like Twelve Data)."""

    def __init__(
        self,
        name: str,
        timestamps: list[datetime],
        values: list[float | tuple[float, ...] | None],
    ):
        self.name = name
        self._timestamps = timestamps
        self._values = values
        self._index: dict[datetime, int] = {ts: i for i, ts in enumerate(timestamps)}
        self._current_idx: int = -1
        self._series: list[float | tuple[float, ...] | None] = []

    def update(self, bar: Bar) -> None:
        """Advance to the bar's timestamp."""
        idx = self._index.get(bar.timestamp)
        if idx is not None:
            self._current_idx = idx
            self._series.append(self._values[idx])
        else:
            # No data for this timestamp - carry forward
            self._series.append(self._series[-1] if self._series else None)

    @property
    def value(self) -> float | tuple[float, ...] | None:
        if self._current_idx < 0 or self._current_idx >= len(self._values):
            return None
        return self._values[self._current_idx]

    @property
    def ready(self) -> bool:
        return self._current_idx >= 0

    def reset(self) -> None:
        self._current_idx = -1
        self._series.clear()

    @property
    def period(self) -> int:
        return 1  # Precomputed, always ready after first data point

    @property
    def series(self) -> list[float | tuple[float, ...] | None]:
        return list(self._series)


class IndicatorPipeline:
    """Composes multiple indicators and feeds them bar data together."""

    def __init__(self, indicators: list[Indicator] | None = None):
        self._indicators: dict[str, Indicator] = {}
        if indicators:
            for ind in indicators:
                self.add(ind)

    def add(self, indicator: Indicator) -> "IndicatorPipeline":
        self._indicators[indicator.name] = indicator
        return self

    def update(self, bar: Bar) -> None:
        """Update all indicators with new bar data."""
        for indicator in self._indicators.values():
            indicator.update(bar)

    def get(self, name: str) -> Indicator:
        if name not in self._indicators:
            raise KeyError(f"Indicator '{name}' not in pipeline. Available: {list(self._indicators.keys())}")
        return self._indicators[name]

    def __getitem__(self, name: str) -> Indicator:
        return self.get(name)

    @property
    def ready(self) -> bool:
        """True when all indicators are ready."""
        return all(ind.ready for ind in self._indicators.values())

    @property
    def warmup_period(self) -> int:
        """Maximum warmup period across all indicators."""
        if not self._indicators:
            return 0
        return max(ind.period for ind in self._indicators.values())

    def reset(self) -> None:
        for ind in self._indicators.values():
            ind.reset()

    @property
    def indicators(self) -> dict[str, Indicator]:
        return dict(self._indicators)

    def __contains__(self, name: str) -> bool:
        return name in self._indicators
