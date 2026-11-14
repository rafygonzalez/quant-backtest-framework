from __future__ import annotations

import csv
import math
from collections import Counter, defaultdict
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from btframework.replay.journal import validate_annotation, journal_rows, legacy_record
from copy import deepcopy

from btframework.account.account import Account
from btframework.account.broker import DefaultBrokerProfile
from btframework.account.lot import LotSpec
from btframework.core.config import BacktestConfig
from btframework.core.engine import BacktestEngine
from btframework.data.models import Bar, Instrument
from btframework.events.market import MarketDataEvent
from btframework.execution.orders import Order
from btframework.types import OrderType, Side
from btframework.replay.indicators import ChartIndicators
from btframework.replay.context import context_chart


def number(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("invalid_number")
    return result


@dataclass(frozen=True)
class InstrumentProfile:
    symbol: str = "MNQ"
    kind: str = "futures"
    tick_size: str = "0.25"
    contract_size: str = "2"
    minimum: str = "1"
    step: str = "1"
    margin_per_contract: str = "1000"
    leverage: str = "1"
    commission: str = "0"
    spread: str = "0"
    currency: str = "USD"

    def __post_init__(self):
        if self.kind not in {"futures", "cfd", "forex"} or not self.symbol or len(self.currency) != 3:
            raise ValueError("invalid_profile")
        for key in ("tick_size", "contract_size", "minimum", "step", "leverage"):
            if number(getattr(self, key)) <= 0:
                raise ValueError("invalid_profile")
        for key in ("margin_per_contract", "commission", "spread"):
            if number(getattr(self, key)) < 0:
                raise ValueError("invalid_profile")
        if self.kind == "futures" and (number(self.minimum) % 1 or number(self.step) % 1
                                       or number(self.margin_per_contract) <= 0):
            raise ValueError("whole_contracts_required")

    def lot(self):
        return LotSpec(min_qty=number(self.minimum), step=number(self.step),
                       tick_size=number(self.tick_size), contract_size=number(self.contract_size))


class ReplayBroker(DefaultBrokerProfile):
    def __init__(self, profile):
        super().__init__()
        self.profile = profile
        self._lot_specs = {profile.symbol: profile.lot()}

    def margin_required(self, instrument, qty, price):
        if self.profile.kind == "futures":
            return qty * number(self.profile.margin_per_contract)
        return price * qty * number(self.profile.contract_size) / number(self.profile.leverage)


@dataclass(frozen=True)
class StrategyEvent:
    event_id: str
    kind: str
    known_at: datetime
    direction: int = 0
    price: float | None = None
    provisional: bool = False


class ReplayFeed:
    def __init__(self, bars, minutes):
        self.bars, self.minutes = bars, minutes

    def __iter__(self):
        for bar in self.bars:
            yield {bar.symbol: MarketDataEvent(
                timestamp=bar.timestamp + timedelta(minutes=self.minutes),
                bar_timestamp=bar.timestamp, symbol=bar.symbol, open=bar.open,
                high=bar.high, low=bar.low, close=bar.close, volume=bar.volume)}


class ReplayCosts:
    def __init__(self, profile, strategy):
        self.profile, self.strategy = profile, strategy

    def process_order(self, order, ctx, next_fn):
        return next_fn(order)

    def process_fill(self, fill, ctx, next_fn):
        if self.strategy.active is None:
            intent = self.strategy.intent
            if (intent is None or (intent["sl"] is not None and intent["direction"] * (fill.fill_price - intent["sl"]) <= 0)
                    or (intent["tp"] is not None and intent["direction"] * (intent["tp"] - fill.fill_price) <= 0)):
                self.strategy.rejected = True
                return None
            required = ctx.account.broker.margin_required(
                Instrument(fill.symbol), fill.quantity, fill.fill_price)
            if ctx.account.free_margin < required + number(self.profile.commission) * fill.quantity:
                self.strategy.rejected = True
                return None
        fill.commission = number(self.profile.commission) * fill.quantity
        return next_fn(fill)


class ManualStrategy:
    def __init__(self):
        self.engine = None
        self.intent = None
        self.active = None
        self.trades = []
        self.rejected = False

    def on_bar(self, event):
        pass

    def on_fill(self, fill):
        if self.active is None:
            intent = self.intent
            risk = abs(fill.fill_price - intent["sl"]) * fill.quantity if intent["sl"] is not None else None
            if risk is not None and risk <= 0:
                raise ValueError("invalid_risk")
            self.active = {**intent, "entry": fill.fill_price, "entry_time": fill.timestamp,
                           "risk": risk * number(intent["contract_size"]) if risk is not None else None,
                           "entry_commission": fill.commission, "quantity": fill.quantity}
            self.bracket(parent=fill)
        else:
            active = self.active
            gross = ((fill.fill_price - active["entry"]) * active["direction"]
                     * active["quantity"] * number(active["contract_size"]))
            net = gross - active["entry_commission"] - fill.commission
            self.trades.append({"symbol": fill.symbol, "side": active["direction"],
                                "entry": active["entry"], "exit": fill.fill_price,
                                "quantity": fill.quantity, "opened_at": active["entry_time"],
                                "closed_at": fill.timestamp, "pnl": net,
                                "r": net / active["risk"] if active["risk"] is not None else None, "risk": active["risk"],
                                "commission": active["entry_commission"] + fill.commission,
                                "event_id": active["event_id"], "event_kind": active["event_kind"]})
            if active.get("journal_id"):
                self.trades[-1]["journal_id"] = active["journal_id"]
            self.active = None
            self.intent = None
            self.engine.cancel_orders(fill.symbol)

    def bracket(self, parent=None):
        active = self.active
        self.engine.cancel_orders(active["symbol"])
        side = Side.SELL if active["direction"] == 1 else Side.BUY
        metadata = {"_oco_group": "manual_bracket"}
        if parent is not None:
            metadata["_parent_fill"] = parent.fill_price
            metadata["_parent_at_open"] = self.intent["order_type"] == "MARKET"
        for kind, field, value in ((OrderType.STOP, "stop_price", active["sl"]),
                                   (OrderType.LIMIT, "price", active["tp"])):
            if value is None:
                continue
            order = Order(symbol=active["symbol"], side=side, quantity=active["quantity"],
                          order_type=kind, metadata=dict(metadata), **{field: value})
            self.engine.submit_order(order, reducing=True)


class ReplaySession:
    def __init__(self, bars: list[Bar], profile: InstrumentProfile, capital=10000,
                 base_minutes=1, events=(), strategy_name="manual",
                 provisional_detector: Callable | None = None, history_bars=None):
        if number(capital) < 10 or base_minutes < 1 or not bars:
            raise ValueError("invalid_session")
        previous = None
        for bar in bars:
            values = (bar.open, bar.high, bar.low, bar.close)
            if (any(not number(v).is_finite() or number(v) <= 0 for v in values)
                    or bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close)
                    or not math.isfinite(bar.volume) or bar.volume < 0
                    or bar.timestamp.second or bar.timestamp.microsecond or (previous is not None and bar.timestamp <= previous)
                    or bar.symbol != profile.symbol):
                raise ValueError("invalid_bars")
            previous = bar.timestamp
        self.bars, self.profile, self.capital = bars, profile, number(capital)
        self.base_minutes, self.strategy_name = base_minutes, strategy_name
        self.context_bars = []
        self.context_minutes = 60
        self.context_source = ""
        self._context_close_times = []
        self.history_bars = []
        self._chart_bars = list(bars)
        self._chart_bar_times = [int(bar.timestamp.replace(tzinfo=timezone.utc).timestamp()) for bar in bars]
        self.events = sorted(events, key=lambda e: (e.known_at, e.event_id))
        self.detector = provisional_detector
        self._chart_cache = {}
        self._chart_indicators = {}
        self.cursor = 0
        self.event_cursor = 0
        self.seen = []
        self.current_events = []
        self.decisions = {}
        self.audit = []
        self.journal_records = []
        self.event_kinds = {e.kind for e in self.events} | set(getattr(provisional_detector, "event_kinds", ()))
        self.selected = set(self.event_kinds)
        self.playing = False
        self.stop_reason = "ready"
        self.strategy = ManualStrategy()
        account = Account(initial_balance=self.capital, currency=profile.currency,
                          broker=ReplayBroker(profile))
        self.engine = (BacktestEngine(BacktestConfig(fail_fast=True))
                       .with_feed(ReplayFeed(bars, base_minutes))
                       .with_account(account).with_strategy(lambda: self.strategy)
                       .with_instruments([Instrument(profile.symbol, asset_class=profile.kind,
                                                     currency=profile.currency)]))
        self.engine.with_middleware(ReplayCosts(profile, self.strategy))
        self.engine.with_fixed_spread(number(profile.spread))
        self.engine.start()
        self.strategy.engine = self.engine
        if history_bars:
            self.add_history(history_bars, events, provisional_detector)

    def add_history(self, history_bars, events, detector=None):
        """Warm charts and signal state without touching execution or decisions."""
        previous = None
        for bar in history_bars:
            if (bar.symbol != self.profile.symbol
                    or bar.timestamp + timedelta(minutes=self.base_minutes) > self.bars[0].timestamp
                    or (previous is not None and bar.timestamp <= previous)):
                raise ValueError("invalid_history")
            values = (bar.open, bar.high, bar.low, bar.close)
            if (any(number(v) <= 0 for v in values) or bar.high < max(bar.open, bar.close)
                    or bar.low > min(bar.open, bar.close) or bar.high < bar.low
                    or not math.isfinite(bar.volume) or bar.volume < 0):
                raise ValueError("invalid_history")
            previous = bar.timestamp
        self.history_bars = list(history_bars)
        self._chart_bars = self.history_bars + self.bars
        self._chart_bar_times = [int(bar.timestamp.replace(tzinfo=timezone.utc).timestamp()) for bar in self._chart_bars]
        self._chart_cache.clear()
        self._chart_indicators.clear()
        self.events = sorted(events, key=lambda e: (e.known_at, e.event_id))
        self.event_cursor = 0
        historical = []
        while self.event_cursor < len(self.events) and self.events[self.event_cursor].known_at <= self.now:
            event = self.events[self.event_cursor]
            if event.known_at <= self.bars[0].timestamp:
                historical.append(event)
            self.event_cursor += 1
        self.detector = detector
        if detector:
            for bar in self.history_bars:
                historical.extend(detector((bar,)))
            for bar in self.bars[:self.cursor]:
                detector((bar,))
        self.seen = list({event.event_id: event for event in historical + self.seen}.values())
        self.seen.sort(key=lambda e: (e.known_at, e.event_id))
        self.event_kinds |= {e.kind for e in self.events} | set(getattr(detector, "event_kinds", ()))

    @property
    def now(self):
        return (self.bars[self.cursor - 1].timestamp + timedelta(minutes=self.base_minutes)
                if self.cursor else self.bars[0].timestamp)

    def _record(self, action, **values):
        self.audit.append({"at": self.now.isoformat(), "cursor": self.cursor,
                           "action": action, **values})

    def _skip_current(self):
        for event in self.current_events:
            self.decisions.setdefault(event.event_id, "skipped")
        self.current_events = []

    def advance(self, steps=1, until_event=False):
        self._skip_current()
        for _ in range(min(int(steps), 10000)):
            if self.cursor >= len(self.bars):
                self.playing = False
                self.stop_reason = "end"
                break
            had_position = self.strategy.active is not None
            self.engine.step()
            self.cursor += 1
            if self.strategy.rejected:
                self.engine.cancel_orders(self.profile.symbol)
                self.strategy.intent = None
                self.strategy.rejected = False
                self.playing = False
                self.stop_reason = "margin_rejected"
            fresh = []
            while self.event_cursor < len(self.events) and self.events[self.event_cursor].known_at <= self.now:
                fresh.append(self.events[self.event_cursor])
                self.event_cursor += 1
            if self.detector:
                fresh.extend(self.detector((self.bars[self.cursor - 1],)))
            self.seen.extend(fresh)
            in_trade = had_position or self.strategy.active is not None
            for event in fresh:
                if in_trade:
                    self.decisions[event.event_id] = "occupied"
            if self.strategy.active and not any(o.strategy_id == "__liquidation__" for o in self.engine.pending_orders):
                self._trail()
            if had_position and self.strategy.active is None:
                self.playing = False
                self.stop_reason = "trade_closed"
                break
            hits = [e for e in fresh if e.kind in self.selected]
            if hits and not in_trade:
                self.current_events = hits
                self.stop_reason = "event"
                self.playing = False
                break
            if self.stop_reason == "margin_rejected":
                break
            self.stop_reason = "playing" if self.playing else "paused"
            if not until_event:
                break
        if self.cursor >= len(self.bars):
            self.playing = False
            self.stop_reason = "end"
        return self.snapshot()

    def _price(self, value):
        price = number(value)
        if price <= 0 or price % number(self.profile.tick_size):
            raise ValueError("invalid_tick")
        return price

    def place(self, side, quantity, order_type, sl=None, tp=None, price=None, event_id=None, journal=None):
        if self.cursor == 0 or self.cursor == len(self.bars):
            raise ValueError("no_executable_bar")
        if self.strategy.active or self.strategy.intent:
            raise ValueError("occupied")
        direction = {"BUY": 1, "SELL": -1}.get(side)
        if direction is None or order_type not in {"MARKET", "LIMIT", "STOP"}:
            raise ValueError("invalid_order")
        quantity = number(quantity)
        valid, _ = self.profile.lot().validate_quantity(quantity)
        if not valid:
            raise ValueError("invalid_quantity")
        current = self.bars[self.cursor - 1].close
        entry = current if order_type == "MARKET" else self._price(price)
        sl = self._price(sl) if sl not in (None, "") else None
        tp = self._price(tp) if tp not in (None, "") else None
        if (sl is not None and direction * (entry - sl) <= 0) or (tp is not None and direction * (tp - entry) <= 0):
            raise ValueError("invalid_bracket")
        if order_type == "LIMIT" and direction * (entry - current) >= 0:
            raise ValueError("invalid_limit")
        if order_type == "STOP" and direction * (entry - current) <= 0:
            raise ValueError("invalid_stop")
        chosen = next((e for e in self.current_events if e.event_id == event_id),
                      self.current_events[0] if self.current_events else None)
        if event_id == "manual":
            chosen = None
        intent = {"symbol": self.profile.symbol, "direction": direction, "sl": sl, "tp": tp,
                  "contract_size": self.profile.contract_size, "order_type": order_type, "price": entry,
                  "event_id": chosen.event_id if chosen else None,
                  "event_kind": chosen.kind if chosen else "manual", "trailing": None}
        record = None
        if journal is not None:
            catalog = getattr(self.detector, "journal_catalog", {"version": "manual_v1", "fields": []})
            annotation = validate_annotation(journal, catalog)
            capture = getattr(self.detector, "journal_context", None)
            record = {"id": "journal:" + str(len(self.journal_records) + 1), "version": 1,
                      "catalog": deepcopy(catalog), "manual": annotation,
                      "decided_at": self.now.isoformat(), "symbol": self.profile.symbol,
                      "context_source": self.context_source,
                      "side": side, "quantity": str(quantity), "order_type": order_type,
                      "planned_entry": str(entry), "initial_sl": str(sl) if sl is not None else None,
                      "initial_tp": str(tp) if tp is not None else None,
                      "engine_context": deepcopy(capture(self.now, direction)) if capture else None}
            intent["journal_id"] = record["id"]
        order = Order(symbol=self.profile.symbol, side=Side[side], quantity=quantity,
                      order_type=OrderType[order_type],
                      price=entry if order_type == "LIMIT" else None,
                      stop_price=entry if order_type == "STOP" else None)
        self.engine.submit_order(order)
        if record is not None:
            self.journal_records.append(record)
        self.strategy.intent = intent
        if chosen:
            self.decisions[chosen.event_id] = "ordered"
        self._record("order", side=side, quantity=str(quantity), order_type=order_type,
                     price=str(entry), sl=str(sl), tp=str(tp), event_id=intent["event_id"])
        return self.snapshot()

    def modify_pending(self, price=None, sl=None, tp=None):
        intent = self.strategy.intent
        if intent is None or self.strategy.active is not None:
            raise ValueError("no_pending")
        order = next((o for o in self.engine.pending_orders if o.is_active), None)
        if order is None:
            raise ValueError("no_pending")
        entry = intent["price"] if price is None else self._price(price)
        stop = intent["sl"] if sl is None else None if sl == "" else self._price(sl)
        target = intent["tp"] if tp is None else None if tp == "" else self._price(tp)
        direction = intent["direction"]
        if (stop is not None and direction * (entry - stop) <= 0) or (target is not None and direction * (target - entry) <= 0):
            raise ValueError("invalid_bracket")
        current = self.bars[self.cursor - 1].close
        if order.order_type == OrderType.LIMIT and direction * (entry - current) >= 0:
            raise ValueError("invalid_limit")
        if order.order_type == OrderType.STOP and direction * (entry - current) <= 0:
            raise ValueError("invalid_stop")
        if not self.engine.account.check_margin(order.symbol, order.quantity, entry):
            raise ValueError("Insufficient margin")
        if order.order_type == OrderType.LIMIT:
            order.price = entry
        elif order.order_type == OrderType.STOP:
            order.stop_price = entry
        intent.update(price=entry, sl=stop, tp=target)
        self._record("modify_pending", price=str(entry), sl=str(stop), tp=str(target))
        return self.snapshot()

    def cancel(self):
        if self.strategy.active:
            raise ValueError("position_open")
        self.engine.cancel_orders(self.profile.symbol)
        if self.strategy.intent and self.strategy.intent["event_id"]:
            self.decisions[self.strategy.intent["event_id"]] = "cancelled"
        self.strategy.intent = None
        self._record("cancel")
        return self.snapshot()

    def modify(self, sl=None, tp=None, trailing=None, break_even=False):
        active = self.strategy.active
        if active is None:
            raise ValueError("no_position")
        stop = active["entry"] if break_even else active["sl"] if sl is None else None if sl == "" else self._price(sl)
        target = active["tp"] if tp is None else None if tp == "" else self._price(tp)
        current, direction = self.bars[self.cursor - 1].close, active["direction"]
        if (stop is not None and direction * (current - stop) <= 0) or (target is not None and direction * (target - current) <= 0):
            raise ValueError("invalid_bracket")
        distance = active["trailing"] if trailing is None else number(trailing)
        if distance is not None and distance < 0:
            raise ValueError("invalid_trailing")
        active.update(sl=stop, tp=target, trailing=distance if distance else None)
        self.strategy.bracket()
        self._record("modify", sl=str(stop), tp=str(target), trailing=str(distance))
        return self.snapshot()

    def _trail(self):
        active = self.strategy.active
        if not active["trailing"]:
            return
        direction, tick = active["direction"], number(self.profile.tick_size)
        candidate = self.bars[self.cursor - 1].close - direction * active["trailing"]
        candidate = (candidate // tick) * tick
        if active["sl"] is None or direction * (candidate - active["sl"]) > 0:
            active["sl"] = candidate
            self.strategy.bracket()

    def close(self):
        active = self.strategy.active
        if not active:
            raise ValueError("no_position")
        self.engine.cancel_orders(self.profile.symbol)
        self.engine.submit_order(Order(symbol=self.profile.symbol,
                                       side=Side.SELL if active["direction"] == 1 else Side.BUY,
                                       quantity=active["quantity"], order_type=OrderType.MARKET), reducing=True)
        self._record("close")
        return self.snapshot()

    def set_context(self, bars, minutes=60, source=""):
        previous=None
        for bar in bars:
            if (bar.symbol != self.profile.symbol or (previous is not None and bar.timestamp<=previous)
                    or bar.high < max(bar.open,bar.close) or bar.low > min(bar.open,bar.close)
                    or any(number(v)<=0 for v in (bar.open,bar.high,bar.low,bar.close))
                    or not math.isfinite(bar.volume) or bar.volume<0):
                raise ValueError("invalid_history")
            previous=bar.timestamp
        if minutes < self.base_minutes or minutes % self.base_minutes:
            raise ValueError("invalid_timeframe")
        self.context_bars=list(bars)
        self.context_minutes=minutes
        self.context_source=source
        self._context_close_times=[int((bar.timestamp+timedelta(minutes=minutes)).replace(tzinfo=timezone.utc).timestamp()) for bar in bars]

    def chart(self, minutes, limit=2000, before=None):
        if minutes < self.base_minutes or minutes % self.base_minutes:
            raise ValueError("invalid_timeframe")
        limit = max(20, min(int(limit), 10000))
        if self.context_bars and minutes>=self.context_minutes and minutes%self.context_minutes==0:
            rows = context_chart(self,minutes,10000 if before is not None else limit)
            return [row for row in rows if before is None or row["time"] <= before][-limit:]
        indicators = self._chart_indicators.setdefault(minutes, ChartIndicators())
        processed, buckets = self._chart_cache.get(minutes, (0, {}))
        visible_count = len(self.history_bars) + self.cursor
        for bar in self._chart_bars[processed:visible_count]:
            stamp = int(bar.timestamp.replace(tzinfo=timezone.utc).timestamp())
            bucket = stamp // (minutes * 60) * minutes * 60
            if bucket not in buckets:
                buckets[bucket] = {"time": bucket, "open": float(bar.open), "high": float(bar.high),
                                   "low": float(bar.low), "close": float(bar.close), "volume": bar.volume,
                                   "_count": 1, "_last": stamp, "_continuous": stamp == bucket, "_index": len(buckets)}
            else:
                row = buckets[bucket]
                row.update(high=max(row["high"], float(bar.high)), low=min(row["low"], float(bar.low)),
                           close=float(bar.close), volume=row["volume"] + bar.volume,
                           _count=row["_count"] + 1,
                           _continuous=row["_continuous"] and stamp == row["_last"] + self.base_minutes * 60,
                           _last=stamp)
            row = buckets[bucket]
            if row['_continuous'] and row['_count'] == minutes // self.base_minutes and not row.get('_indicators_done'):
                row.update(indicators.update(row))
                row['_indicators_done'] = True
        self._chart_cache[minutes] = (visible_count, buckets)
        now = int(self.now.replace(tzinfo=timezone.utc).timestamp())
        expected = minutes // self.base_minutes
        rows = []
        for row in reversed(buckets.values()):
            if before is not None and row["time"] > before:
                continue
            complete = row["_continuous"] and row["_count"] == expected
            forming = row["_continuous"] and row["time"] + minutes * 60 > now
            if complete or forming:
                rows.append({key: value for key, value in row.items() if not key.startswith("_")})
                if not complete:
                    rows[-1].update(indicators.preview(row))
                rows[-1]["complete"] = complete
                rows[-1]["index"] = row["_index"]
                if len(rows) == limit:
                    break
        rows.reverse()
        return rows

    def chart_visuals(self, minutes, rows, at=None):
        if not rows or self.detector is None or not hasattr(self.detector, "visuals"):
            return {"ranges": [], "stage": None}
        return self.detector.visuals(at or self.now, rows[0]["time"], rows[-1]["time"], minutes)

    def scoreboard(self):
        trades = self.strategy.trades
        def metrics(items):
            pnl = [float(t["pnl"]) for t in items]
            gains, losses = sum(max(v, 0) for v in pnl), -sum(min(v, 0) for v in pnl)
            r_values = [float(t["r"]) for t in items if t["r"] is not None]
            return {"trades": len(items), "net": sum(pnl),
                    "win_rate": sum(v > 0 for v in pnl) / len(pnl) if pnl else None,
                    "profit_factor": gains / losses if losses else None,
                    "expectancy_r": sum(r_values) / len(r_values) if r_values else None,
                    "r_trades": len(r_values), "without_initial_risk": len(items)-len(r_values),
                    "total_r": sum(r_values) if r_values or not items else None,
                    "wins": sum(v > 0 for v in pnl), "losses": sum(v < 0 for v in pnl),
                    "break_even": sum(v == 0 for v in pnl),
                    "avg_win": gains / sum(v > 0 for v in pnl) if gains else None,
                    "avg_loss": -losses / sum(v < 0 for v in pnl) if losses else None,
                    "best_r": max(r_values, default=None),
                    "worst_r": min(r_values, default=None),
                    "commission": sum(float(t["commission"]) for t in items)}
        groups = defaultdict(list)
        for trade in trades:
            groups[trade["event_kind"]].append(trade)
        processed, peak, drawdown, drawdown_pct = getattr(self, "_drawdown_cache", (0, float(self.capital), 0.0, 0.0))
        curve = self.engine.account.equity_curve
        for _, equity in curve[processed:]:
            peak = max(peak, float(equity))
            drawdown = max(drawdown, peak - float(equity))
            drawdown_pct = max(drawdown_pct, (peak - float(equity)) / peak if peak else 0)
        self._drawdown_cache = (len(curve), peak, drawdown, drawdown_pct)
        return {**metrics(trades), "drawdown": drawdown, "drawdown_pct": drawdown_pct,
                "by_event": {k: metrics(v) for k, v in groups.items()},
                "opportunities": dict(Counter(self.decisions.values())),
                "open_positions": len(self.engine.account.open_positions),
                "pending_orders": len(self.engine.pending_orders)}

    def update_journal(self, journal_id, journal, expected_revision, edited_at=None):
        record = next((record for record in self.journal_records if record["id"] == journal_id), None)
        legacy = None
        if record is None and isinstance(journal_id, str) and journal_id.startswith("legacy-trade:"):
            try:
                index = int(journal_id.split(":", 1)[1])
                if index < 0:
                    raise ValueError
                legacy = self.strategy.trades[index]
            except (ValueError, IndexError):
                raise ValueError("journal_not_found") from None
            if legacy.get("journal_id"):
                raise ValueError("journal_conflict")
            record = legacy_record(index, legacy, getattr(self.detector, "journal_catalog", {"version": "manual_v1", "fields": []}))
        if record is None:
            raise ValueError("journal_not_found")
        revision = len(record.get("revisions", []))
        if type(expected_revision) is not int or expected_revision != revision:
            raise ValueError("journal_conflict")
        annotation = validate_annotation(journal, record["catalog"])
        stamp = edited_at or datetime.now(timezone.utc).isoformat()
        datetime.fromisoformat(stamp)
        if legacy is not None:
            legacy["journal_id"] = record["id"]
            self.journal_records.append(record)
        record.setdefault("revisions", []).append({"manual": annotation, "edited_at": stamp,
            "replay_at": self.now.isoformat(), "after_entry": True,
            "after_close": any(trade.get("journal_id") == journal_id for trade in self.strategy.trades)})
        return stamp

    def journal_snapshot(self):
        return journal_rows(self.journal_records, self.strategy.trades, self.strategy.active, self.strategy.intent,
                            getattr(self.detector, "journal_catalog", {"version": "manual_v1", "fields": []}))

    def snapshot(self, compact=False, include_journal=True):
        if getattr(self, "_quiet", False):
            return {}
        processed, gaps, last_jump = getattr(self, "_coverage_cache", (1, 0, 0))
        for i in range(processed, self.cursor):
            jump = (self.bars[i].timestamp-self.bars[i-1].timestamp).total_seconds()/60-self.base_minutes
            if jump > 0:
                gaps += 1
                last_jump = int(jump)
        self._coverage_cache = (max(1, self.cursor), gaps, last_jump)
        return {"journal": self.journal_snapshot() if include_journal and not compact else [],
                "journal_count": len(self.journal_records),
                "journal_catalog": getattr(self.detector, "journal_catalog", {"version": "manual_v1", "fields": []}),
                "market_price": self.bars[self.cursor-1].close if self.cursor else None,
                "coverage": {"gaps": gaps, "last_jump_minutes": last_jump},
                "cursor": self.cursor, "total": len(self.bars), "now": self.now,
                "history_bars": len(self.history_bars), "replay_start": self.bars[0].timestamp,
                "context_source": self.context_source, "context_minutes": self.context_minutes,
                "playing": self.playing, "reason": self.stop_reason,
                "profile": asdict(self.profile), "strategy": self.strategy_name, "base_minutes": self.base_minutes,
                "account": {**self.engine.account.snapshot(), "margin_level": (self.engine.account.margin_level
                     if self.engine.account.margin_used else None)},
                "equity_curve": [] if compact else self.engine.account.equity_curve, "position": self.strategy.active,
                "intent": self.strategy.intent,
                "pending": [asdict(o) for o in self.engine.pending_orders],
                "events": [asdict(e) for e in self.current_events],
                "markers": [asdict(e) for e in self.seen[-300:]], "selected": sorted(self.selected),
                "available_events": sorted(self.event_kinds),
                "scoreboard": self.scoreboard(), "trades": self.strategy.trades,
                "decisions": {} if compact else self.decisions, "audit": [] if compact else self.audit}


def load_csv(path: Path, symbol: str) -> list[Bar]:
    with path.open(newline="") as stream:
        return load_csv_file(stream, symbol)


def load_csv_file(stream, symbol: str) -> list[Bar]:
    bars = []
    for row in csv.DictReader(stream):
        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        if timestamp.tzinfo:
            timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        bars.append(Bar(timestamp=timestamp, symbol=symbol,
                        **{key: number(row[key]) for key in ("open", "high", "low", "close")},
                        volume=float(row.get("volume") or 0)))
    return bars
