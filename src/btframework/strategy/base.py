from __future__ import annotations
from typing import Protocol, runtime_checkable
from btframework.events.market import MarketDataEvent
from btframework.events.fill import FillEvent


@runtime_checkable
class Strategy(Protocol):
    """Protocol for trading strategies."""
    strategy_id: str

    def on_bar(self, event: MarketDataEvent) -> None:
        """Called on each new bar. Generate signals via self.context."""
        ...

    def warmup_period(self) -> int:
        """Number of bars needed before strategy is active."""
        ...


class StrategyContext:
    """Context provided to strategies for emitting signals and accessing state."""

    def __init__(self):
        self._signals: list = []
        self._indicators: dict = {}
        self._account: object | None = None
        self._engine: object | None = None

    def emit_signal(self, signal) -> None:
        """Emit a trading signal."""
        self._signals.append(signal)

    def pop_signals(self) -> list:
        """Retrieve and clear pending signals."""
        signals = self._signals
        self._signals = []
        return signals

    @property
    def indicators(self):
        return self._indicators

    @indicators.setter
    def indicators(self, value):
        self._indicators = value

    @property
    def account(self):
        return self._account

    @account.setter
    def account(self, value):
        self._account = value
