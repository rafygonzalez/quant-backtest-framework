from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from btframework.types import Side, PositionSide, Price, Quantity
from btframework.account.broker import BrokerProfile, DefaultBrokerProfile
from btframework.account.lot import LotSpec
from btframework.data.models import Instrument
from btframework.execution.fill import Fill
from btframework.exceptions import InsufficientFundsError

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Tracks an open position."""
    symbol: str
    side: PositionSide
    quantity: Quantity
    avg_entry_price: Price
    opened_at: datetime
    strategy_id: str = ""
    contract_size: Decimal = Decimal("1")
    currency: str = "USD"
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")

    @property
    def position_key(self) -> str:
        """Unique key for hedging mode: symbol + strategy_id."""
        return f"{self.symbol}::{self.strategy_id}"

    def update_price(self, current_price: Price, fx_rate: Decimal = Decimal("1")) -> None:
        """Update unrealized PnL with current price."""
        if self.side == PositionSide.LONG:
            self.unrealized_pnl = (
                (current_price - self.avg_entry_price)
                * self.quantity * self.contract_size * fx_rate
            )
        elif self.side == PositionSide.SHORT:
            self.unrealized_pnl = (
                (self.avg_entry_price - current_price)
                * self.quantity * self.contract_size * fx_rate
            )


class Account:
    """Tracks account state: balance, positions, margin, equity curve.

    Supports two position modes:
      - "netting" (default): one position per symbol, weighted average entry.
      - "hedging": multiple positions per symbol, keyed by (symbol, strategy_id).
        Each strategy manages its own position independently.

    Supports contract_size-aware PnL and multi-currency accounting.
    """

    def __init__(
        self,
        initial_balance: Decimal | float | int = 100_000,
        currency: str = "USD",
        broker: BrokerProfile | None = None,
        margin_call_level: float = 100.0,
        maintenance_level: float = 50.0,
        position_mode: str = "netting",
    ):
        self._initial_balance = Decimal(str(initial_balance))
        self._balance = self._initial_balance
        self._currency = currency
        self._broker = broker or DefaultBrokerProfile()
        self._positions: dict[str, Position] = {}
        self._closed_trades: list[dict] = []
        self._equity_curve: list[tuple[datetime, Decimal]] = []
        self._margin_used = Decimal("0")
        self._instruments: dict[str, Instrument] = {}
        self._margin_call_level = margin_call_level
        self._maintenance_level = maintenance_level
        self._fx_rates: dict[str, Decimal] = {}
        self._position_mode = position_mode

    @property
    def position_mode(self) -> str:
        return self._position_mode

    @property
    def initial_balance(self) -> Decimal:
        return self._initial_balance

    @property
    def currency(self) -> str:
        return self._currency

    @property
    def broker(self) -> BrokerProfile:
        return self._broker

    @property
    def balance(self) -> Decimal:
        return self._balance

    @property
    def equity(self) -> Decimal:
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        return self._balance + unrealized

    @property
    def margin_used(self) -> Decimal:
        return self._margin_used

    @property
    def free_margin(self) -> Decimal:
        return self.equity - self._margin_used

    @property
    def margin_level(self) -> float:
        if self._margin_used == 0:
            return float("inf")
        return float(self.equity / self._margin_used * 100)

    @property
    def open_positions(self) -> list[Position]:
        return list(self._positions.values())

    @property
    def closed_trades(self) -> list[dict]:
        return list(self._closed_trades)

    @property
    def equity_curve(self) -> list[tuple[datetime, Decimal]]:
        return list(self._equity_curve)

    def register_instrument(self, instrument: Instrument) -> None:
        """Register an instrument for margin and contract_size calculations."""
        self._instruments[instrument.symbol] = instrument

    def set_fx_rates(self, rates: dict[str, Decimal]) -> None:
        """Set currency conversion rates to account currency."""
        self._fx_rates.update(rates)

    def _get_fx_rate(self, currency: str) -> Decimal:
        if currency == self._currency:
            return Decimal("1")
        return self._fx_rates.get(currency, Decimal("1"))

    def _get_contract_size(self, symbol: str) -> Decimal:
        if symbol in self._instruments:
            return self._broker.lot_spec(self._instruments[symbol]).contract_size
        return Decimal("1")

    def _get_instrument_currency(self, symbol: str) -> str:
        if symbol in self._instruments:
            return self._instruments[symbol].currency
        return self._currency

    def _pos_key(self, symbol: str, strategy_id: str = "") -> str:
        """Generate position dict key based on mode."""
        if self._position_mode == "hedging":
            return f"{symbol}::{strategy_id}"
        return symbol

    def check_margin(self, symbol: str, qty: Quantity, price: Price) -> bool:
        """Check if there's enough free margin for a new order."""
        if symbol not in self._instruments:
            return True
        instrument = self._instruments[symbol]
        required = self._broker.margin_required(instrument, qty, price)
        return self.free_margin >= required

    def process_fill(self, fill: Fill, timestamp: datetime | None = None) -> None:
        """Update account state based on a fill."""
        ts = timestamp or fill.timestamp
        key = self._pos_key(fill.symbol, fill.strategy_id)

        # Deduct commission
        self._balance -= fill.commission

        if key in self._positions:
            self._update_position(key, fill, ts)
        else:
            self._open_position(key, fill, ts)

    def _open_position(self, key: str, fill: Fill, timestamp: datetime) -> None:
        """Open a new position."""
        side = PositionSide.LONG if fill.side == Side.BUY else PositionSide.SHORT
        contract_size = self._get_contract_size(fill.symbol)
        currency = self._get_instrument_currency(fill.symbol)

        self._positions[key] = Position(
            symbol=fill.symbol,
            side=side,
            quantity=fill.quantity,
            avg_entry_price=fill.fill_price,
            opened_at=timestamp,
            strategy_id=fill.strategy_id,
            contract_size=contract_size,
            currency=currency,
        )
        logger.info(f"Opened {side.name} position: {fill.symbol} qty={fill.quantity} @ {fill.fill_price}")

    def _update_position(self, key: str, fill: Fill, timestamp: datetime) -> None:
        """Update or close an existing position. Supports position reversal."""
        pos = self._positions[key]
        is_closing = (
            (pos.side == PositionSide.LONG and fill.side == Side.SELL) or
            (pos.side == PositionSide.SHORT and fill.side == Side.BUY)
        )

        if is_closing:
            close_qty = min(fill.quantity, pos.quantity)
            fx_rate = self._get_fx_rate(pos.currency)

            # Calculate realized PnL (in account currency)
            if pos.side == PositionSide.LONG:
                pnl = (fill.fill_price - pos.avg_entry_price) * close_qty * pos.contract_size * fx_rate
            else:
                pnl = (pos.avg_entry_price - fill.fill_price) * close_qty * pos.contract_size * fx_rate

            self._balance += pnl
            pos.realized_pnl += pnl

            remaining = pos.quantity - close_qty
            excess = fill.quantity - close_qty

            # Record closed trade
            self._closed_trades.append({
                "symbol": pos.symbol,
                "side": pos.side.name,
                "entry_price": pos.avg_entry_price,
                "exit_price": fill.fill_price,
                "quantity": close_qty,
                "pnl": pnl,
                "contract_size": pos.contract_size,
                "currency": pos.currency,
                "fx_rate": fx_rate,
                "opened_at": pos.opened_at,
                "closed_at": timestamp,
                "strategy_id": pos.strategy_id,
            })

            if remaining > 0:
                pos.quantity = remaining
                logger.info(f"Partial close: {fill.symbol} remaining={remaining}")
            else:
                del self._positions[key]
                logger.info(f"Closed position: {fill.symbol} PnL={pnl}")

            # Position reversal
            if excess > 0:
                reversal_side = PositionSide.LONG if fill.side == Side.BUY else PositionSide.SHORT
                contract_size = self._get_contract_size(fill.symbol)
                currency = self._get_instrument_currency(fill.symbol)
                self._positions[key] = Position(
                    symbol=fill.symbol,
                    side=reversal_side,
                    quantity=excess,
                    avg_entry_price=fill.fill_price,
                    opened_at=timestamp,
                    strategy_id=fill.strategy_id,
                    contract_size=contract_size,
                    currency=currency,
                )
                logger.info(
                    f"Position reversal: {fill.symbol} opened {reversal_side.name} "
                    f"qty={excess} @ {fill.fill_price}"
                )
        else:
            # Adding to position - update average entry
            total_cost = pos.avg_entry_price * pos.quantity + fill.fill_price * fill.quantity
            pos.quantity += fill.quantity
            pos.avg_entry_price = total_cost / pos.quantity

    def update_market_prices(
        self,
        prices: dict[str, Price],
        timestamp: datetime,
        fx_rates: dict[str, Decimal] | None = None,
    ) -> str | None:
        """Update unrealized PnL and equity curve with current prices.

        Returns "margin_call" or "liquidation" if margin level drops below
        thresholds, or None if OK.
        """
        if fx_rates:
            self._fx_rates.update(fx_rates)

        for key, pos in self._positions.items():
            if pos.symbol in prices:
                fx_rate = self._get_fx_rate(pos.currency)
                pos.update_price(prices[pos.symbol], fx_rate)

        self._equity_curve.append((timestamp, self.equity))

        # Recalculate margin used from instruments
        self._margin_used = Decimal("0")
        for key, pos in self._positions.items():
            if pos.symbol in self._instruments and pos.symbol in prices:
                instrument = self._instruments[pos.symbol]
                self._margin_used += self._broker.margin_required(
                    instrument, pos.quantity, prices[pos.symbol]
                )

        # Check margin levels
        if self._margin_used > 0:
            level = self.margin_level
            if level < self._maintenance_level:
                return "liquidation"
            elif level < self._margin_call_level:
                return "margin_call"

        return None

    def get_position(self, symbol: str, strategy_id: str = "") -> Position | None:
        """Get a specific position. In netting mode, strategy_id is ignored."""
        key = self._pos_key(symbol, strategy_id)
        return self._positions.get(key)

    def get_positions_for_symbol(self, symbol: str) -> list[Position]:
        """Get all positions for a symbol (useful in hedging mode)."""
        return [p for p in self._positions.values() if p.symbol == symbol]

    def snapshot(self) -> dict:
        return {
            "balance": self._balance,
            "equity": self.equity,
            "margin_used": self._margin_used,
            "free_margin": self.free_margin,
            "margin_level": self.margin_level,
            "open_positions": len(self._positions),
            "closed_trades": len(self._closed_trades),
            "position_mode": self._position_mode,
        }

    def reset(self) -> None:
        self._balance = self._initial_balance
        self._positions.clear()
        self._closed_trades.clear()
        self._equity_curve.clear()
        self._margin_used = Decimal("0")
        self._fx_rates.clear()
