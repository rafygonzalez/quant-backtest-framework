from __future__ import annotations
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from btframework.core.config import BacktestConfig
from btframework.core.registry import ComponentRegistry, registry as global_registry
from btframework.core.middleware import MiddlewarePipeline, ExecutionContext
from btframework.core.hooks import HookManager
from btframework.core.clock import SimulatedClock
from btframework.events.base import EventBus
from btframework.events.market import MarketDataEvent
from btframework.events.log import EventLogger
from btframework.data.feed import HistoricalDataFeed, DataFeed
from btframework.data.models import Bar, Instrument
from btframework.data.calendar import TradingCalendar
from btframework.execution.engine import ExecutionEngine
from btframework.execution.exchange import SimulatedExchange
from btframework.execution.exchange_v2 import IntraBarExchange
from btframework.execution.orders import Order
from btframework.execution.price_path import PricePathGenerator
from btframework.execution.sizing import PositionSizer
from btframework.execution.middlewares.spread import SpreadProfile
from btframework.account.account import Account
from btframework.strategy.indicators.base import IndicatorPipeline, Indicator
from btframework.types import Side, OrderType
from btframework.exceptions import ConfigError
from btframework.charts.engine import ChartEngine
from btframework.analytics.tearsheet import generate_tearsheet

logger = logging.getLogger(__name__)


class BacktestResult:
    """Container for backtest results."""

    def __init__(
        self,
        account: Account,
        event_bus: EventBus,
        config: BacktestConfig | None = None,
        ohlcv_data: dict[str, list] | None = None,
        indicators: "IndicatorPipeline | None" = None,
    ):
        self.account = account
        self.event_bus = event_bus
        self.config = config
        self.ohlcv_data = ohlcv_data or {}
        self.indicators = indicators
        self._chart_engine = None

    @property
    def equity_curve(self) -> list[tuple[datetime, Decimal]]:
        return self.account.equity_curve

    @property
    def trades(self) -> list[dict]:
        return self.account.closed_trades

    def summary(self) -> dict:
        """Return summary statistics."""
        trades = self.trades
        if not trades:
            return {
                "total_trades": 0,
                "final_equity": float(self.account.equity),
                "return_pct": 0.0,
            }

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)

        return {
            "total_trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": len(wins) / len(trades) if trades else 0,
            "total_pnl": float(total_pnl),
            "avg_win": float(sum(t["pnl"] for t in wins) / len(wins)) if wins else 0,
            "avg_loss": float(sum(t["pnl"] for t in losses) / len(losses)) if losses else 0,
            "final_equity": float(self.account.equity),
            "return_pct": float((self.account.equity - self.account.initial_balance) / self.account.initial_balance * 100),
        }

    def show_chart(self, **kwargs) -> None:
        """Open interactive chart in browser."""
        if self._chart_engine is None:
            self._chart_engine = ChartEngine(self)
        self._chart_engine.show(**kwargs)

    def save_report(self, path: str | Path) -> None:
        """Save HTML tearsheet report."""
        generate_tearsheet(self, path)


class BacktestEngine:
    """Main orchestrator for backtests. Uses builder pattern for configuration."""

    def __init__(self, config: BacktestConfig | None = None):
        self._config = config or BacktestConfig()
        self._registry = global_registry
        self._event_bus = EventBus()
        self._hooks = HookManager()
        self._clock = SimulatedClock(strict=False)
        self._pipeline = MiddlewarePipeline()
        self._exchange = SimulatedExchange()
        self._account: Account | None = None
        self._feed: DataFeed | None = None
        self._strategy: Any = None
        self._strategy_cls: type | None = None
        self._strategy_params: dict = {}
        self._indicators: IndicatorPipeline = IndicatorPipeline()
        self._event_logger: EventLogger | None = None
        self._stopped = False
        self._ohlcv_data: dict[str, list] = {}
        self._instruments: list[Instrument] = []
        self._calendar: TradingCalendar | None = None
        self._sizer: PositionSizer | None = None
        self._spread_profile: SpreadProfile | None = None
        self._fixed_half_spread: Decimal | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "BacktestEngine":
        """Create engine from YAML config file."""
        config = BacktestConfig.from_yaml(path)
        engine = cls(config)
        engine._build_from_config()
        return engine

    @property
    def hooks(self) -> HookManager:
        return self._hooks

    @property
    def execution_pipeline(self) -> MiddlewarePipeline:
        return self._pipeline

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def registry(self) -> ComponentRegistry:
        return self._registry

    # --- Builder methods ---

    def with_data(
        self,
        provider: Any,
        symbols: list[str],
        timeframe: str = "1d",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> "BacktestEngine":
        """Configure data feed."""
        self._feed = HistoricalDataFeed(
            provider=provider,
            symbols=symbols,
            start=start or datetime(2020, 1, 1),
            end=end or datetime.now(),
            timeframe=timeframe,
        )
        return self

    def with_feed(self, feed: DataFeed) -> "BacktestEngine":
        """Use a custom DataFeed directly."""
        self._feed = feed
        return self

    def with_strategy(self, strategy_cls: type, params: dict | None = None) -> "BacktestEngine":
        """Configure strategy."""
        self._strategy_cls = strategy_cls
        self._strategy_params = params or {}
        return self

    def with_account(self, account: Account) -> "BacktestEngine":
        """Configure account."""
        self._account = account
        return self

    def with_indicators(self, indicators: list[Indicator]) -> "BacktestEngine":
        """Add indicators to the pipeline."""
        for ind in indicators:
            self._indicators.add(ind)
        return self

    def with_middleware(self, middleware: Any) -> "BacktestEngine":
        """Add execution middleware."""
        self._pipeline.use(middleware)
        return self

    def with_instruments(self, instruments: list[Instrument]) -> "BacktestEngine":
        """Register instruments for margin calculations."""
        self._instruments = list(instruments)
        return self

    def with_event_logging(self) -> "BacktestEngine":
        """Enable event logging for replay/debug."""
        self._event_logger = EventLogger(self._event_bus)
        return self

    def with_calendar(self, calendar: TradingCalendar) -> "BacktestEngine":
        """Set a trading calendar for market hours filtering."""
        self._calendar = calendar
        return self

    def with_intra_bar(self, generator: PricePathGenerator | None = None) -> "BacktestEngine":
        """Use intra-bar price path simulation for order execution."""
        self._exchange = IntraBarExchange(path_generator=generator)
        return self

    def with_sizer(self, sizer: PositionSizer) -> "BacktestEngine":
        """Set a position sizing strategy."""
        self._sizer = sizer
        return self

    def with_bid_ask_spread(self, profile: SpreadProfile | None = None) -> "BacktestEngine":
        """Enable native bid/ask spread on the exchange.

        Requires a calendar to be set (for session-dependent spreads).
        The spread is applied directly in the exchange fill logic:
          BUY orders fill at ask (mid + half_spread)
          SELL orders fill at bid (mid - half_spread)
          Limit/stop triggers use the correct side of the book.

        This is more accurate than VariableSpreadMiddleware which only
        adjusts fill prices post-hoc without affecting order triggering.
        """
        self._spread_profile = profile or SpreadProfile.default_forex()
        return self

    def with_fixed_spread(self, spread: Decimal) -> "BacktestEngine":
        spread = Decimal(str(spread))
        if not spread.is_finite() or spread < 0:
            raise ConfigError("Invalid spread")
        self._fixed_half_spread = spread / 2
        return self

    def stop(self) -> None:
        """Stop the backtest (can be called from hooks)."""
        self._stopped = True

    # --- Run ---

    def start(self) -> "BacktestEngine":
        """Initialize a fresh session without consuming its feed."""
        self._validate()
        self._reset()
        self._setup()
        self._feed_iterator = iter(self._feed)
        self._bar_count = 0
        self._warmup = self._strategy.warmup_period() if hasattr(self._strategy, "warmup_period") else 0
        self._finished = False
        self._started = True
        logger.info("Starting backtest: %s", self._config.name)
        self._hooks.emit("on_backtest_start", engine=self)
        return self

    @property
    def finished(self) -> bool:
        return getattr(self, "_finished", False)

    @property
    def account(self) -> Account:
        return self._account

    @property
    def pending_orders(self) -> list[Order]:
        return self._exchange.pending_orders

    def submit_order(self, order: Order, *, reducing: bool = False) -> None:
        """Submit at the replay cursor, eligible from the next bar."""
        if not getattr(self, "_started", False) or self.finished:
            raise ConfigError("Session is not active")
        if not order.quantity.is_finite() or order.quantity <= 0:
            raise ConfigError("Invalid quantity")
        valid, reason = self._account.broker.validate_order(order)
        if not valid:
            raise ConfigError(reason)
        if reducing:
            pos = self._account.get_position(order.symbol, order.strategy_id)
            expected = Side.SELL if pos and pos.side.name == "LONG" else Side.BUY
            if pos is None or order.side != expected or order.quantity > pos.quantity:
                raise ConfigError("Invalid reducing order")
        else:
            instrument = next((i for i in self._instruments if i.symbol == order.symbol), None)
            if instrument is None:
                raise ConfigError("Unknown instrument")
            valid, reason = self._account.broker.lot_spec(instrument).validate_quantity(order.quantity)
            if not valid:
                raise ConfigError(reason)
            price = order.price or order.stop_price
            if price is None and self._ohlcv_data.get(order.symbol):
                price = Decimal(str(self._ohlcv_data[order.symbol][-1]["close"]))
            if price is None or not self._account.check_margin(order.symbol, order.quantity, price):
                raise ConfigError("Insufficient margin")
        self._exchange.submit_order(order)

    def cancel_orders(self, symbol: str | None = None) -> int:
        return self._exchange.cancel_all(symbol)

    def step(self) -> bool:
        """Consume one feed step; return False when exhausted or stopped."""
        if not getattr(self, "_started", False):
            self.start()
        if self.finished:
            return False
        if self._stopped:
            self._finish()
            return False
        try:
            bar_events = next(self._feed_iterator)
        except StopIteration:
            self._finish()
            return False
        self._bar_count += 1

        for symbol, event in bar_events.items():
            # Create Bar from event
            bar = Bar(
                timestamp=event.bar_timestamp or event.timestamp,
                open=event.open,
                high=event.high,
                low=event.low,
                close=event.close,
                volume=event.volume,
                symbol=symbol,
            )

            # Collect OHLCV for charts
            if symbol not in self._ohlcv_data:
                self._ohlcv_data[symbol] = []
            self._ohlcv_data[symbol].append({
                "timestamp": bar.timestamp,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": bar.volume,
            })

            # Advance clock
            self._clock.advance(bar.timestamp)

            # Update indicators
            self._indicators.update(bar)

            # Publish market data event
            self._event_bus.publish(event)
            self._hooks.emit("before_bar", event=event, bar=bar)

            # Check calendar for market hours
            market_open = True
            active_sessions = []
            if self._calendar is not None:
                market_open = self._calendar.is_market_open(bar.timestamp)
                if market_open:
                    active_sessions = self._calendar.active_sessions(bar.timestamp)

            # Compute bid/ask spread for this bar
            half_spread = Decimal("0")
            if self._spread_profile is not None and self._calendar is not None:
                spread_pips = self._compute_spread_pips(active_sessions)
                half_spread = Decimal(str(spread_pips)) * self._spread_profile.pip_value / 2
            if self._fixed_half_spread is not None:
                half_spread = self._fixed_half_spread
            self._exchange.half_spread = half_spread

            # Build execution context
            ctx_metadata = {}
            if active_sessions:
                ctx_metadata["sessions"] = active_sessions
            if half_spread > 0:
                ctx_metadata["half_spread"] = half_spread
                ctx_metadata["spread_price"] = half_spread * 2
                if self._spread_profile is not None and self._fixed_half_spread is None:
                    ctx_metadata["spread_pips"] = float(spread_pips)
            ctx = ExecutionContext(
                timestamp=bar.timestamp,
                symbol=symbol,
                current_price=bar.close,
                volume=bar.volume,
                account=self._account,
                metadata=ctx_metadata,
            )

            # Process pending orders from previous bars (no look-ahead)
            fills = self._execution_engine.process_bar(symbol, bar, ctx)
            self._dispatch_fills(fills, bar, ctx, self._config.fail_fast)

            # A bracket is submitted from on_fill — at the fill instant, not at this
            # bar's close — so it protects the position for the rest of this bar.
            # Deferring it would leave an entry without a stop for its whole fill bar.
            if fills:
                self._dispatch_fills(
                    self._execution_engine.process_bar_late(symbol, bar, ctx),
                    bar, ctx, self._config.fail_fast)

            # Update account prices and check margin
            prices = {symbol: bar.close}
            margin_status = self._account.update_market_prices(prices, bar.timestamp)

            if margin_status == "margin_call":
                self._hooks.emit("on_margin_call", account=self._account, timestamp=bar.timestamp)
            elif margin_status == "liquidation":
                self._hooks.emit("on_margin_call", account=self._account, timestamp=bar.timestamp)
                self._liquidate_all(bar, ctx)

            # If market closed, skip strategy and order submission
            if not market_open:
                self._hooks.emit("on_market_closed", event=event, bar=bar, timestamp=bar.timestamp)
                self._hooks.emit("after_bar", event=event, bar=bar)
                continue

            # Run strategy (only after warmup)
            if self._bar_count > self._warmup:
                self._hooks.emit("before_signal", event=event)
                try:
                    self._strategy.on_bar(event)
                except Exception as e:
                    if self._config.fail_fast:
                        raise
                    logger.error(f"Strategy error on bar {self._bar_count}: {e}", exc_info=True)
                    self._hooks.emit("on_strategy_error", error=e, event=event, bar_index=self._bar_count)
                    continue

                # Collect signals from strategy context
                if hasattr(self._strategy, 'context'):
                    signals = self._strategy.context.pop_signals()
                    for signal in signals:
                        self._hooks.emit("after_signal", signal=signal)
                        self._execution_engine.process_signal(signal, ctx)

            self._hooks.emit("after_bar", event=event, bar=bar)

        return True

    def _finish(self) -> None:
        if not self._finished:
            self._finished = True
            self._hooks.emit("on_backtest_end", engine=self)
            logger.info("Backtest complete. %s bars processed.", self._bar_count)

    def result(self) -> BacktestResult:
        return BacktestResult(
            account=self._account,
            event_bus=self._event_bus,
            config=self._config,
            ohlcv_data=self._ohlcv_data,
            indicators=self._indicators,
        )

    def run(self) -> BacktestResult:
        """Execute a fresh backtest using the resumable bar loop."""
        self.start()
        while self.step():
            pass
        return self.result()

    def _dispatch_fills(self, fills, bar: Bar, ctx: ExecutionContext, fail_fast: bool) -> None:
        """Book each fill on the account and hand it to the strategy."""
        for fill in fills:
            self._hooks.emit("after_fill", fill=fill, ctx=ctx)
            try:
                self._account.process_fill(fill, bar.timestamp)
            except Exception as e:
                if fail_fast:
                    raise
                logger.error(f"Fill processing error: {e}", exc_info=True)
                self._hooks.emit("on_fill_error", error=e, fill=fill)
                continue
            if hasattr(self._strategy, 'on_fill'):
                self._strategy.on_fill(fill)

    def _liquidate_all(self, bar: Bar, ctx: ExecutionContext) -> None:
        """Cancel all pending orders and close all positions via MARKET orders."""
        self._exchange.cancel_all()
        for pos in self._account.open_positions:
            side = Side.SELL if pos.side.name == "LONG" else Side.BUY
            close_order = Order(
                symbol=pos.symbol,
                side=side,
                quantity=pos.quantity,
                order_type=OrderType.MARKET,
                strategy_id="__liquidation__",
            )
            self._exchange.submit_order(close_order)
        logger.warning(f"Liquidation triggered: all positions will be closed on next bar")

    def _reset(self) -> None:
        """Full state reset — guarantees run() x2 gives the same result."""
        self._clock.reset()
        self._exchange.reset()
        if self._account is not None:
            self._account.reset()
        self._ohlcv_data.clear()
        self._indicators.reset()
        self._strategy = None
        self._stopped = False

    def _validate(self) -> None:
        """Validate engine is properly configured."""
        if self._feed is None:
            raise ConfigError("No data feed configured. Use .with_data() or .with_feed()")
        if self._strategy_cls is None and self._strategy is None:
            raise ConfigError("No strategy configured. Use .with_strategy()")
        if self._account is None:
            self._account = Account()

    def _setup(self) -> None:
        """Initialize components before run."""
        # Instantiate strategy (always fresh after _reset sets it to None)
        if self._strategy_cls:
            self._strategy = self._strategy_cls(**self._strategy_params)

        # Set up strategy context
        if hasattr(self._strategy, 'context'):
            self._strategy.context.indicators = self._indicators
            self._strategy.context.account = self._account

        # Register instruments on account
        for instrument in self._instruments:
            self._account.register_instrument(instrument)

        # Create execution engine
        self._execution_engine = ExecutionEngine(
            exchange=self._exchange,
            pipeline=self._pipeline,
            event_bus=self._event_bus,
            account=self._account,
            calendar=self._calendar,
            sizer=self._sizer,
        )

    def _compute_spread_pips(self, active_sessions) -> float:
        """Compute spread in pips from active sessions and profile."""
        if not self._spread_profile:
            return 0.0
        if not active_sessions:
            return self._spread_profile.off_hours_pips
        spreads = []
        for session in active_sessions:
            cfg = self._spread_profile.session_spreads.get(session)
            if cfg:
                spreads.append(cfg.effective_pips)
        if not spreads:
            return self._spread_profile.off_hours_pips
        return min(spreads)

    def _build_from_config(self) -> None:
        """Build engine from BacktestConfig (YAML)."""
        cfg = self._config

        # Account
        self._account = Account(
            initial_balance=Decimal(str(cfg.account.initial_balance)),
            currency=cfg.account.currency,
        )

        # Middlewares
        for mw_cfg in cfg.execution.middlewares:
            try:
                mw_cls = self._registry.get("middleware", mw_cfg.name)
                self._pipeline.use(mw_cls(**mw_cfg.params))
            except Exception as e:
                logger.warning(f"Could not load middleware '{mw_cfg.name}': {e}")

        # Strategy
        if cfg.strategy.name:
            try:
                self._strategy_cls = self._registry.get("strategy", cfg.strategy.name)
                self._strategy_params = cfg.strategy.params
            except Exception as e:
                logger.warning(f"Could not load strategy '{cfg.strategy.name}': {e}")
