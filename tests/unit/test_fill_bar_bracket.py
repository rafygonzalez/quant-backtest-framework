"""A bracket submitted from a fill callback protects the bar that produced the fill.

Brackets are submitted from the strategy's on_fill, i.e. at the fill instant. Queuing
them like a close-of-bar order left every entry unprotected for its whole fill bar: a
bar that crossed the stop exited at the NEXT bar's open instead, always beyond the stop.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from btframework.data.models import Bar
from btframework.execution.exchange import SimulatedExchange
from btframework.execution.orders import Order
from btframework.types import OrderType, Side, TimeInForce

SYMBOL = "TEST"


def _bar(o: str, h: str, l: str, c: str, day: int = 2) -> Bar:
    return Bar(
        timestamp=datetime(2024, 1, day),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        symbol=SYMBOL,
    )


def _market(side: Side) -> Order:
    return Order(symbol=SYMBOL, side=side, quantity=Decimal("1"),
                 order_type=OrderType.MARKET)


def _stop(side: Side, price: str, oco: str | None = None) -> Order:
    return Order(symbol=SYMBOL, side=side, quantity=Decimal("1"),
                 order_type=OrderType.STOP, stop_price=Decimal(price),
                 time_in_force=TimeInForce.GTC,
                 metadata={"_oco_group": oco} if oco else {})


def _limit(side: Side, price: str, oco: str | None = None) -> Order:
    return Order(symbol=SYMBOL, side=side, quantity=Decimal("1"),
                 order_type=OrderType.LIMIT, price=Decimal(price),
                 time_in_force=TimeInForce.GTC,
                 metadata={"_oco_group": oco} if oco else {})


def _fill_entry(ex: SimulatedExchange, bar: Bar, side: Side = Side.BUY) -> Decimal:
    """Run the entry through process_bar exactly as the engine does."""
    ex.submit_order(_market(side))
    candidates = ex.process_bar(SYMBOL, bar)
    assert len(candidates) == 1
    order, fill = candidates[0]
    ex.confirm_fill(order, fill)
    return fill.fill_price


class TestFillBarBracket:

    def test_stop_crossed_on_the_fill_bar_fills_at_the_stop(self):
        ex = SimulatedExchange()
        bar = _bar("100", "101", "90", "91")
        assert _fill_entry(ex, bar) == Decimal("100")

        ex.submit_order(_stop(Side.SELL, "95"))
        late = ex.process_bar_late(SYMBOL, bar)

        assert len(late) == 1
        assert late[0][1].fill_price == Decimal("95")

    def test_untouched_bracket_survives_to_the_next_bar(self):
        ex = SimulatedExchange()
        bar = _bar("100", "101", "99", "100")
        _fill_entry(ex, bar)

        ex.submit_order(_stop(Side.SELL, "95"))
        assert ex.process_bar_late(SYMBOL, bar) == []
        assert len(ex.pending_orders) == 1

        later = _bar("100", "100", "90", "91", day=3)
        candidates = ex.process_bar(SYMBOL, later)
        assert len(candidates) == 1
        assert candidates[0][1].fill_price == Decimal("95")

    def test_late_pass_does_not_advance_the_bar_index(self):
        ex = SimulatedExchange()
        bar = _bar("100", "101", "99", "100")
        _fill_entry(ex, bar)
        index = ex.bar_index

        ex.submit_order(_stop(Side.SELL, "95"))
        ex.process_bar_late(SYMBOL, bar)

        assert ex.bar_index == index

    def test_orders_from_earlier_bars_are_not_rematched(self):
        """The late pass is only for what was submitted during this bar: re-running the
        whole book would fill a resting order twice against the same range."""
        ex = SimulatedExchange()
        ex.submit_order(_stop(Side.SELL, "95"))
        quiet = _bar("100", "101", "99", "100")
        assert ex.process_bar(SYMBOL, quiet) == []

        crossing = _bar("100", "101", "90", "91", day=3)
        candidates = ex.process_bar(SYMBOL, crossing)
        assert len(candidates) == 1
        ex.confirm_fill(*candidates[0])
        # The resting stop filled in process_bar; nothing was submitted during this bar.
        assert ex.process_bar_late(SYMBOL, crossing) == []

    def test_oco_leaves_one_side_when_the_bar_covers_both(self):
        ex = SimulatedExchange()
        bar = _bar("100", "110", "90", "105")
        _fill_entry(ex, bar)

        ex.submit_order(_limit(Side.SELL, "108", oco="g"))
        ex.submit_order(_stop(Side.SELL, "95", oco="g"))
        late = ex.process_bar_late(SYMBOL, bar)

        assert len(late) == 1
        # Bullish bar: assumed path O→L→H, so the stop below the market resolves first.
        assert late[0][1].fill_price == Decimal("95")
        assert ex.pending_orders == []

    def test_short_bracket_is_live_on_its_fill_bar(self):
        ex = SimulatedExchange()
        bar = _bar("100", "110", "99", "109")
        assert _fill_entry(ex, bar, side=Side.SELL) == Decimal("100")

        ex.submit_order(_stop(Side.BUY, "105"))
        late = ex.process_bar_late(SYMBOL, bar)

        assert len(late) == 1
        assert late[0][1].fill_price == Decimal("105")

    @pytest.mark.parametrize("hs,expected", [("0", "95"), ("0.5", "95")])
    def test_half_spread_applies_to_the_late_pass(self, hs, expected):
        ex = SimulatedExchange()
        ex.half_spread = Decimal(hs)
        bar = _bar("100", "101", "90", "91")
        _fill_entry(ex, bar)

        ex.submit_order(_stop(Side.SELL, expected))
        late = ex.process_bar_late(SYMBOL, bar)

        assert len(late) == 1
        # STOP SELL triggers on bid_low and fills at the stop while the bar runs past it.
        assert late[0][1].fill_price == Decimal(expected)


class TestFillBarOnlyReadsWhatFollowsTheFill:
    """A mid-bar entry leaves one side of its bar behind it, and that side is not ours.

    The case that motivated this is real: an AUD/USD limit buy filled at 0.684636 on a
    bar whose HIGH equalled its OPEN, then collected a target at that high — a price the
    market only visited before the position existed.
    """

    def _entry_at(self, ex, bar, side, order):
        ex.submit_order(order)
        cands = ex.process_bar(SYMBOL, bar)
        assert len(cands) == 1
        o, f = cands[0]
        ex.confirm_fill(o, f)
        return f.fill_price

    def test_target_above_a_dip_fill_is_not_taken_on_that_bar(self):
        ex = SimulatedExchange()
        # High == open: the high is behind any fill below the open.
        bar = _bar("100", "100", "90", "95")
        fill = self._entry_at(ex, bar, Side.BUY, _limit(Side.BUY, "92"))
        assert fill == Decimal("92")

        tp = _limit(Side.SELL, "99")
        tp.metadata["_parent_fill"] = fill
        ex.submit_order(tp)

        assert ex.process_bar_late(SYMBOL, bar) == []
        assert len(ex.pending_orders) == 1  # it rests, it is not lost

    def test_stop_below_a_dip_fill_is_still_taken(self):
        ex = SimulatedExchange()
        bar = _bar("100", "100", "90", "95")
        fill = self._entry_at(ex, bar, Side.BUY, _limit(Side.BUY, "92"))

        sl = _stop(Side.SELL, "91")
        sl.metadata["_parent_fill"] = fill
        ex.submit_order(sl)

        late = ex.process_bar_late(SYMBOL, bar)
        assert len(late) == 1
        assert late[0][1].fill_price == Decimal("91")

    def test_a_rally_fill_reverses_which_side_is_readable(self):
        ex = SimulatedExchange()
        # Low == open: the low is behind any fill above the open.
        bar = _bar("100", "110", "100", "105")
        fill = self._entry_at(ex, bar, Side.BUY, _stop(Side.BUY, "103"))
        assert fill == Decimal("103")

        sl = _stop(Side.SELL, "101")
        sl.metadata["_parent_fill"] = fill
        tp = _limit(Side.SELL, "108")
        tp.metadata["_parent_fill"] = fill
        ex.submit_order(sl)
        ex.submit_order(tp)

        late = ex.process_bar_late(SYMBOL, bar)
        assert len(late) == 1
        assert late[0][1].fill_price == Decimal("108")   # the high is ahead
        assert len(ex.pending_orders) == 1               # the stop rests

    def test_an_open_fill_still_reads_the_whole_bar(self):
        ex = SimulatedExchange()
        bar = _bar("100", "101", "90", "91")
        fill = self._entry_at(ex, bar, Side.BUY, _market(Side.BUY))
        assert fill == Decimal("100")

        sl = _stop(Side.SELL, "95")
        sl.metadata["_parent_fill"] = fill
        ex.submit_order(sl)

        late = ex.process_bar_late(SYMBOL, bar)
        assert len(late) == 1
        assert late[0][1].fill_price == Decimal("95")

    def test_a_cost_shifted_open_fill_is_declared_by_the_submitter(self):
        """Cost is applied to fills by middleware AFTER the exchange prices them, so a
        market entry reaches the bracket displaced from bar.open. The price alone can no
        longer prove it was an open fill — the submitter has to say so."""
        ex = SimulatedExchange()
        bar = _bar("100", "101", "90", "91")
        shifted = Decimal("100.125")          # what a cost middleware hands back

        blind = _stop(Side.SELL, "95")
        blind.metadata["_parent_fill"] = shifted
        ex.submit_order(blind)
        assert ex.process_bar_late(SYMBOL, bar) == []   # read as a rally fill, stop held back

        declared = _stop(Side.SELL, "95")
        declared.metadata["_parent_fill"] = shifted
        declared.metadata["_parent_at_open"] = True
        ex.submit_order(declared)
        late = ex.process_bar_late(SYMBOL, bar)
        assert len(late) == 1
        assert late[0][1].fill_price == Decimal("95")

    def test_an_order_with_no_parent_is_unaffected(self):
        ex = SimulatedExchange()
        bar = _bar("100", "101", "90", "91")
        self._entry_at(ex, bar, Side.BUY, _market(Side.BUY))
        ex.submit_order(_stop(Side.SELL, "95"))
        assert len(ex.process_bar_late(SYMBOL, bar)) == 1
