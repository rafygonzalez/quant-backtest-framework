from __future__ import annotations
from btframework.data.models import Bar


class SMA:
    """Simple Moving Average - minimal reference implementation."""

    def __init__(self, period: int = 20, source: str = "close"):
        self.name = f"sma_{period}"
        self._period = period
        self._source = source
        self._values: list[float] = []
        self._series: list[float | None] = []

    def update(self, bar: Bar) -> None:
        price = float(getattr(bar, self._source))
        self._values.append(price)
        if len(self._values) >= self._period:
            avg = sum(self._values[-self._period:]) / self._period
            self._series.append(avg)
        else:
            self._series.append(None)

    @property
    def value(self) -> float | None:
        return self._series[-1] if self._series else None

    @property
    def ready(self) -> bool:
        return len(self._values) >= self._period

    def reset(self) -> None:
        self._values.clear()
        self._series.clear()

    @property
    def period(self) -> int:
        return self._period

    @property
    def series(self) -> list[float | None]:
        return list(self._series)
