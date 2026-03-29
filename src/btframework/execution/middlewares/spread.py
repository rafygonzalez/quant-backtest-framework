"""Variable spread middleware — adjusts fill prices based on active trading session.

Spreads are tighter during high-liquidity sessions (London, NY overlap)
and wider during off-hours.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from btframework.types import Side
from btframework.core.middleware import ExecutionContext
from btframework.data.calendar import TradingSession, TradingCalendar, ForexCalendar


@dataclass
class SessionSpreadConfig:
    """Spread configuration for a single session."""
    base_spread_pips: float = 1.0
    multiplier: float = 1.0

    @property
    def effective_pips(self) -> float:
        return self.base_spread_pips * self.multiplier


@dataclass
class SpreadProfile:
    """Spread profile mapping sessions to spread configurations."""
    session_spreads: dict[TradingSession, SessionSpreadConfig] = field(default_factory=dict)
    off_hours_multiplier: float = 3.0
    pip_value: Decimal = Decimal("0.0001")

    @classmethod
    def default_forex(cls) -> SpreadProfile:
        """Default forex spread profile (Exness-style).

        London: 0.8 pips (most liquid)
        NY: 1.0 pips
        Tokyo: 1.2 pips
        Sydney: 1.5 pips
        Off-hours: base 1.5 * 3.0 = 4.5 pips
        """
        return cls(
            session_spreads={
                TradingSession.LONDON: SessionSpreadConfig(base_spread_pips=0.8),
                TradingSession.NEW_YORK: SessionSpreadConfig(base_spread_pips=1.0),
                TradingSession.TOKYO: SessionSpreadConfig(base_spread_pips=1.2),
                TradingSession.SYDNEY: SessionSpreadConfig(base_spread_pips=1.5),
            },
            off_hours_multiplier=3.0,
        )

    @property
    def off_hours_pips(self) -> float:
        """Spread during off-hours (market open but no active session)."""
        base = 1.5  # default base
        if self.session_spreads:
            base = max(c.effective_pips for c in self.session_spreads.values())
        return base * self.off_hours_multiplier


class VariableSpreadMiddleware:
    """Adjusts fill prices based on session-dependent spreads.

    BUY fills get half-spread added (pay ask).
    SELL fills get half-spread subtracted (receive bid).
    """

    def __init__(
        self,
        profile: SpreadProfile | None = None,
        calendar: TradingCalendar | None = None,
    ):
        self._profile = profile or SpreadProfile.default_forex()
        self._calendar = calendar or ForexCalendar()

    def process_order(self, order, ctx: ExecutionContext, next_fn):
        return next_fn(order)

    def process_fill(self, fill, ctx: ExecutionContext, next_fn):
        sessions = self._calendar.active_sessions(ctx.timestamp)
        spread_pips = self._calculate_spread_pips(sessions)
        spread = Decimal(str(spread_pips)) * self._profile.pip_value
        half_spread = spread / 2

        if fill.side == Side.BUY:
            fill.fill_price = fill.fill_price + half_spread
        else:
            fill.fill_price = fill.fill_price - half_spread

        # Store spread info in fill metadata
        if hasattr(fill, 'metadata'):
            fill.metadata["spread_pips"] = float(spread_pips)
            fill.metadata["sessions"] = [s.value for s in sessions]

        return next_fn(fill)

    def _calculate_spread_pips(self, sessions: list[TradingSession]) -> float:
        """Calculate effective spread in pips based on active sessions.

        Overlap (multiple sessions): use the tightest (minimum) spread.
        Single session: use that session's spread.
        No active session: use off-hours spread.
        """
        if not sessions:
            return self._profile.off_hours_pips

        spreads = []
        for session in sessions:
            cfg = self._profile.session_spreads.get(session)
            if cfg:
                spreads.append(cfg.effective_pips)

        if not spreads:
            return self._profile.off_hours_pips

        # Overlap = tightest spread (most liquid)
        return min(spreads)
