from __future__ import annotations
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SimulatedClock:
    """Simulated clock for backtesting - tracks current bar time.

    In strict mode (default=True), out-of-order timestamps raise ValueError.
    In lenient mode (strict=False), duplicates are silently skipped and
    out-of-order timestamps log a warning and skip.
    """

    def __init__(self, start: datetime | None = None, strict: bool = True):
        self._current: datetime | None = start
        self._bar_index: int = 0
        self._strict = strict

    @property
    def now(self) -> datetime:
        if self._current is None:
            raise RuntimeError("Clock not started. Call advance() first.")
        return self._current

    @property
    def bar_index(self) -> int:
        return self._bar_index

    @property
    def strict(self) -> bool:
        return self._strict

    def advance(self, timestamp: datetime) -> bool:
        """Advance clock to the given timestamp.

        Returns True if the clock advanced, False if the timestamp was skipped.
        """
        if self._current is not None:
            if timestamp == self._current:
                # Duplicate timestamp — skip silently
                return False
            if timestamp < self._current:
                if self._strict:
                    raise ValueError(f"Cannot go back in time: {timestamp} <= {self._current}")
                else:
                    logger.warning(f"Out-of-order timestamp skipped: {timestamp} < {self._current}")
                    return False

        self._current = timestamp
        self._bar_index += 1
        return True

    def reset(self) -> None:
        self._current = None
        self._bar_index = 0

    @property
    def started(self) -> bool:
        return self._current is not None
