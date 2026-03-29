"""
btframework.analytics.performance
===================================
High-precision performance analytics for backtested and live strategies.

Accepts both formats natively:
  · btframework Account.closed_trades  — fields: pnl, opened_at, closed_at
  · Quantira trades_log                — fields: pnl_money, entry_time, exit_time,
                                         exit_type, rr_achieved, bars_in_position, audit

Mathematical standards
──────────────────────
Sharpe / Sortino
  Per-trade return series: r_i = pnl_i / equity_before_i.
  Annualised via √(trades_per_year) inferred from the calendar span of the
  sample. ddof=1 (Bessel correction) throughout.
  Sortino downside deviation: σ_down = stdev({r_i : r_i < 0}), MAR = 0.
  Both return None when N < 2 or σ = 0.

Calmar
  CAGR / |max_drawdown_pct| where CAGR = (V_final/V_0)^(365.25/days) − 1.
  Returns None when max_dd = 0 or calendar span < 2 days.

Kelly Criterion  (edge / odds formulation)
  p  = win rate,  b = avg_win / avg_loss_abs   (payoff ratio)
  f* = p − (1 − p) / b
  Interpretation: fraction of capital to risk per trade.
  half_kelly = f* / 2  (recommended for live sizing).
  Returns None when b = 0 or no losses exist.

SQN  (Van Tharp's System Quality Number)
  Risk unit proxy = mean(|losing PnL|) when per-trade initial risk is
  not recorded. R_i = pnl_i / risk_unit.
  SQN = √N × mean(R) / stdev(R, ddof=1).
  Scale: ≥ 3.0 superb · ≥ 2.5 excellent · ≥ 2.0 good · ≥ 1.6 average · < 1.0 poor.
  Returns None when N < 2 or stdev(R) = 0.

VaR / CVaR  (historical, non-parametric)
  Sorted on per-trade PnL (not equity-curve daily returns).
  VaR_α  = ⌊(1−α)·N⌋-th smallest trade PnL.
  CVaR_α = mean of the ⌊(1−α)·N⌋ worst trades  (Expected Shortfall).
  Reported at α = 0.95 and α = 0.99.
  Both are negative numbers when losses exist.

Max Drawdown
  Peak-to-trough on cumulative equity curve ordered by exit timestamp.
  All drawdown episodes are enumerated, not just the maximum.
  'underwater_trades' = bars from peak to full recovery or end of sample.

Profit Factor
  Σ(winning PnL) / Σ(|losing PnL|).  Capped at 999.0 (all-win edge case).

Expectancy
  E[PnL] = p_win · avg_win + p_loss · avg_loss   (avg_loss negative).
  Expectancy-R: same on R-multiple series when available.

Confidence note
  With N < 30 trades, all ratio metrics (Sharpe, Sortino, SQN) have wide
  confidence intervals: SE(Sharpe) ≈ √((1 + Sharpe²/2) / N).
  Treat them as directional signals, not statistically precise estimates.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Internal normalised trade
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Trade:
    pnl:        float
    is_win:     bool
    entry_dt:   Optional[datetime]
    exit_dt:    Optional[datetime]
    r_multiple: Optional[float]     # Realised R; None if not available
    bars:       Optional[int]
    strategy:   str
    regime:     str
    scenario:   str                 # "long" | "short" | "unknown"


def _parse_dt(val) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    try:
        s = str(val).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _normalise(raw: List[dict]) -> List[_Trade]:
    """
    Convert either btframework closed_trades or Quantira trades_log to _Trade list.
    Sorted ascending by exit timestamp so equity-curve arithmetic is well-defined.
    """
    out: List[_Trade] = []
    for t in raw:
        pnl     = float(t.get("pnl_money", t.get("pnl", 0.0)))
        is_win  = (t.get("exit_type", "") == "tp"
                   if "exit_type" in t
                   else pnl > 0)
        audit   = t.get("audit", {})
        audit   = audit if isinstance(audit, dict) else {}
        r_raw   = t.get("rr_achieved")
        out.append(_Trade(
            pnl        = pnl,
            is_win     = is_win,
            entry_dt   = _parse_dt(t.get("entry_time",  t.get("opened_at"))),
            exit_dt    = _parse_dt(t.get("exit_time",   t.get("closed_at"))),
            r_multiple = float(r_raw) if r_raw is not None else None,
            bars       = t.get("bars_in_position"),
            strategy   = audit.get("strategy", t.get("strategy_id", "unknown")),
            regime     = audit.get("regime",   "unknown"),
            scenario   = t.get("scenario",     t.get("side", "unknown")),
        ))
    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(out, key=lambda x: x.exit_dt or _epoch)


# ──────────────────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DrawdownPeriod:
    """One complete peak-to-trough-to-recovery drawdown episode."""
    drawdown_abs:        float           # Currency loss from peak
    drawdown_pct:        float           # Loss as % of peak equity
    peak_equity:         float
    trough_equity:       float
    peak_trade_idx:      int
    trough_trade_idx:    int
    recovery_trade_idx:  Optional[int]  # None = still underwater at end of sample
    peak_date:           Optional[str]
    trough_date:         Optional[str]
    recovery_date:       Optional[str]
    duration_trades:     int            # Trades from peak to trough
    underwater_trades:   int            # Trades from peak to recovery (or end)


@dataclass(frozen=True)
class MonthlyBucket:
    period:     str     # "YYYY-MM"
    pnl:        float
    return_pct: float   # PnL / equity at start of month × 100
    n_trades:   int
    win_rate:   float   # 0.0–1.0


@dataclass
class PerformanceReport:
    """
    Complete performance analytics for one backtest or live-trading period.
    Constructed exclusively by ``compute_performance()``.
    Use ``.print_report()`` for a formatted terminal summary or
    ``.to_dict()`` for JSON / downstream consumption.
    """

    # ── Capital ───────────────────────────────────────────────────────────────
    initial_capital:        float
    final_equity:           float
    total_pnl:              float
    return_pct:             float           # total_pnl / initial_capital × 100
    cagr_pct:               Optional[float] # None when span < 2 calendar days

    # ── Trade counts ──────────────────────────────────────────────────────────
    total_trades:           int
    winning_trades:         int
    losing_trades:          int
    breakeven_trades:       int
    win_rate_pct:           float           # 0–100
    loss_rate_pct:          float

    # ── Risk-adjusted returns ─────────────────────────────────────────────────
    sharpe_ratio:           Optional[float]
    sortino_ratio:          Optional[float]
    calmar_ratio:           Optional[float]

    # ── Drawdown ──────────────────────────────────────────────────────────────
    max_drawdown_abs:               float
    max_drawdown_pct:               float
    max_drawdown_duration_trades:   int     # Peak → trough of worst episode
    max_underwater_trades:          int     # Peak → recovery of worst episode
    current_drawdown_pct:           float   # Drawdown from last peak to final equity
    all_drawdowns:                  List[DrawdownPeriod]

    # ── Profitability ──────────────────────────────────────────────────────────
    profit_factor:          float           # Σwins / Σ|losses|; 999.0 if no losses
    expectancy:             float           # E[PnL per trade] in currency
    expectancy_r:           Optional[float] # E[R-multiple]; None if unavailable
    recovery_factor:        float           # total_pnl / max_dd_abs; 0.0 if no dd
    sqn:                    Optional[float] # Van Tharp SQN; None if N < 2

    # ── Kelly ─────────────────────────────────────────────────────────────────
    kelly_pct:              Optional[float] # Full Kelly as % of capital
    half_kelly_pct:         Optional[float] # f*/2 — recommended live sizing

    # ── Win / Loss distribution ───────────────────────────────────────────────
    avg_win:                float
    avg_loss:               float           # Negative
    avg_rr_achieved:        float
    median_win:             float
    median_loss:            float           # Negative
    largest_win:            float
    largest_loss:           float           # Most negative trade
    pnl_std:                float           # Standard deviation of trade PnL
    avg_bars_in_trade:      Optional[float]

    # ── Risk: VaR / CVaR ─────────────────────────────────────────────────────
    var_95:                 float           # 5th-pctile trade PnL (negative)
    var_99:                 float           # 1st-pctile trade PnL (negative)
    cvar_95:                float           # Mean of worst 5% trades (negative)
    cvar_99:                float           # Mean of worst 1% trades (negative)

    # ── Streaks ───────────────────────────────────────────────────────────────
    max_consecutive_wins:   int
    max_consecutive_losses: int
    current_streak:         int             # Positive = win streak; negative = loss streak

    # ── Breakdown tables ──────────────────────────────────────────────────────
    by_strategy:            Dict[str, dict] # name → {n, pnl, wr_pct, avg_rr}
    by_regime:              Dict[str, dict] # regime → {n, pnl, wr_pct}
    by_scenario:            Dict[str, dict] # long/short → {n, pnl, wr_pct}
    monthly:                List[MonthlyBucket]

    # ─────────────────────────────────────────────────────────────────────────

    def print_report(self, title: str = "Performance Report", width: int = 72) -> None:
        """Pretty-print the full analytics report to stdout."""
        _print_report(self, title, width)

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible plain dict."""
        return _to_dict(self)


# ──────────────────────────────────────────────────────────────────────────────
# Core computation
# ──────────────────────────────────────────────────────────────────────────────

def compute_performance(
    trades:          List[dict],
    initial_capital: float,
    risk_free_rate:  float = 0.0,
) -> PerformanceReport:
    """
    Build a PerformanceReport from a raw list of trade dicts.

    Parameters
    ----------
    trades : List[dict]
        Either btframework ``Account.closed_trades`` or Quantira ``trades_log``.
    initial_capital : float
        Starting capital for equity-curve construction.
    risk_free_rate : float
        Annual risk-free rate (default 0.0). Used in Sharpe / Sortino.
    """
    ts = _normalise(trades)

    if not ts:
        return _empty_report(initial_capital)

    # ── Equity curve ──────────────────────────────────────────────────────────
    # equity[i] = capital after the i-th trade exits (index 0 = initial).
    equity: List[float] = [initial_capital]
    for t in ts:
        equity.append(equity[-1] + t.pnl)

    final_equity = equity[-1]
    total_pnl    = final_equity - initial_capital
    return_pct   = total_pnl / initial_capital * 100

    # ── Calendar span ─────────────────────────────────────────────────────────
    first_dt = next((t.exit_dt for t in ts if t.exit_dt), None)
    last_dt  = next((t.exit_dt for t in reversed(ts) if t.exit_dt), None)
    calendar_days: Optional[float] = None
    if first_dt and last_dt and last_dt > first_dt:
        calendar_days = (last_dt - first_dt).total_seconds() / 86_400

    cagr_pct: Optional[float] = None
    if calendar_days and calendar_days >= 2 and initial_capital > 0 and final_equity > 0:
        cagr_pct = ((final_equity / initial_capital) ** (365.25 / calendar_days) - 1) * 100

    # ── Trade classification ───────────────────────────────────────────────────
    wins  = [t for t in ts if t.is_win]
    losses = [t for t in ts if not t.is_win and t.pnl != 0.0]
    beven  = [t for t in ts if not t.is_win and t.pnl == 0.0]

    n  = len(ts)
    nw = len(wins)
    nl = len(losses)
    nb = len(beven)

    win_rate_pct  = nw / n * 100
    loss_rate_pct = nl / n * 100

    # ── Win / Loss distribution ───────────────────────────────────────────────
    win_pnls  = [t.pnl for t in wins]
    loss_pnls = [t.pnl for t in losses]   # All negative
    all_pnls  = [t.pnl for t in ts]

    avg_win  = statistics.mean(win_pnls)  if win_pnls  else 0.0
    avg_loss = statistics.mean(loss_pnls) if loss_pnls else 0.0

    median_win  = statistics.median(win_pnls)  if win_pnls  else 0.0
    median_loss = statistics.median(loss_pnls) if loss_pnls else 0.0

    largest_win  = max(win_pnls)  if win_pnls  else 0.0
    largest_loss = min(loss_pnls) if loss_pnls else 0.0

    pnl_std = statistics.stdev(all_pnls) if len(all_pnls) >= 2 else 0.0

    # ── Average RR achieved ───────────────────────────────────────────────────
    r_vals = [t.r_multiple for t in ts if t.r_multiple is not None]
    avg_rr_achieved = statistics.mean(r_vals) if r_vals else 0.0

    # ── Bars in trade ─────────────────────────────────────────────────────────
    bars_vals = [t.bars for t in ts if t.bars is not None]
    avg_bars_in_trade = statistics.mean(bars_vals) if bars_vals else None

    # ── Profit Factor ─────────────────────────────────────────────────────────
    gross_profit = sum(p for p in win_pnls)
    gross_loss   = abs(sum(p for p in loss_pnls))
    if gross_loss == 0.0:
        profit_factor = 999.0
    else:
        profit_factor = gross_profit / gross_loss

    # ── Expectancy ────────────────────────────────────────────────────────────
    p_win  = nw / n
    p_loss = nl / n
    expectancy = p_win * avg_win + p_loss * avg_loss

    # Expectancy-R (using r_multiple if available, else proxy via pnl / avg_loss_abs)
    risk_unit = abs(avg_loss) if avg_loss != 0.0 else None
    if r_vals and len(r_vals) >= 2:
        r_wins  = [t.r_multiple for t in wins  if t.r_multiple is not None]
        r_loss  = [t.r_multiple for t in losses if t.r_multiple is not None]
        if r_wins or r_loss:
            avg_r_win  = statistics.mean(r_wins)  if r_wins  else 0.0
            avg_r_loss = statistics.mean(r_loss)  if r_loss  else 0.0
            expectancy_r: Optional[float] = p_win * avg_r_win + p_loss * avg_r_loss
        else:
            expectancy_r = None
    elif risk_unit and risk_unit > 0:
        # Proxy R = pnl / avg_loss_abs
        proxy_r = [t.pnl / risk_unit for t in ts]
        expectancy_r = statistics.mean(proxy_r)
    else:
        expectancy_r = None

    # ── Per-trade returns for Sharpe / Sortino ────────────────────────────────
    # r_i = pnl_i / equity_before_i  (equity[i] is before trade i+1)
    per_trade_returns: List[float] = []
    for i, t in enumerate(ts):
        eb = equity[i]
        if eb > 0:
            per_trade_returns.append(t.pnl / eb)

    # Annualisation: estimate trades_per_year from sample
    trades_per_year: Optional[float] = None
    if calendar_days and calendar_days > 0:
        trades_per_year = n / calendar_days * 365.25

    sharpe_ratio:  Optional[float] = None
    sortino_ratio: Optional[float] = None

    if len(per_trade_returns) >= 2:
        mu  = statistics.mean(per_trade_returns)
        sig = statistics.stdev(per_trade_returns)   # ddof=1

        # Adjust risk-free to per-trade frequency
        rf_per_trade = (risk_free_rate / trades_per_year
                        if trades_per_year and trades_per_year > 0
                        else 0.0)
        excess_mu = mu - rf_per_trade

        ann_factor = math.sqrt(trades_per_year) if trades_per_year else 1.0

        if sig > 0:
            sharpe_ratio = (excess_mu / sig) * ann_factor

        # Sortino: downside σ over negative returns only
        downside = [r for r in per_trade_returns if r < 0]
        if len(downside) >= 2:
            sig_down = statistics.stdev(downside)
            if sig_down > 0:
                sortino_ratio = (excess_mu / sig_down) * ann_factor
        elif not downside:
            # No losing trades: Sortino = +inf; signal with a large sentinel
            sortino_ratio = None   # Semantically infinite; caller can handle

    # ── Calmar ────────────────────────────────────────────────────────────────
    calmar_ratio: Optional[float] = None

    # ── Drawdown analysis ─────────────────────────────────────────────────────
    all_dds, mdd_abs, mdd_pct, mdd_dur, mdd_under = _compute_drawdowns(equity, ts)

    current_peak = max(equity)
    current_dd_pct = (current_peak - final_equity) / current_peak * 100 if current_peak > 0 else 0.0

    recovery_factor = (total_pnl / mdd_abs) if mdd_abs > 0 else 0.0

    if cagr_pct is not None and mdd_pct > 0:
        calmar_ratio = (cagr_pct / 100) / (mdd_pct / 100)

    # ── SQN ──────────────────────────────────────────────────────────────────
    sqn: Optional[float] = None
    if risk_unit and risk_unit > 0 and n >= 2:
        r_series = [t.pnl / risk_unit for t in ts]
        mu_r  = statistics.mean(r_series)
        sig_r = statistics.stdev(r_series)
        if sig_r > 0:
            sqn = math.sqrt(n) * mu_r / sig_r

    # ── Kelly ─────────────────────────────────────────────────────────────────
    kelly_pct:      Optional[float] = None
    half_kelly_pct: Optional[float] = None

    if avg_win > 0 and avg_loss != 0.0:
        b = avg_win / abs(avg_loss)          # payoff ratio
        f_star = p_win - (1 - p_win) / b    # edge / odds
        kelly_pct = max(0.0, f_star) * 100
        half_kelly_pct = kelly_pct / 2

    # ── VaR / CVaR ────────────────────────────────────────────────────────────
    var_95, var_99, cvar_95, cvar_99 = _compute_var_cvar(all_pnls)

    # ── Streaks ───────────────────────────────────────────────────────────────
    max_cons_wins, max_cons_losses, current_streak = _compute_streaks(ts)

    # ── Breakdown tables ──────────────────────────────────────────────────────
    by_strategy = _breakdown(ts, key="strategy")
    by_regime   = _breakdown(ts, key="regime")
    by_scenario = _breakdown(ts, key="scenario")

    # ── Monthly buckets ───────────────────────────────────────────────────────
    monthly = _monthly_buckets(ts, initial_capital, equity)

    return PerformanceReport(
        initial_capital             = initial_capital,
        final_equity                = final_equity,
        total_pnl                   = total_pnl,
        return_pct                  = return_pct,
        cagr_pct                    = cagr_pct,
        total_trades                = n,
        winning_trades              = nw,
        losing_trades               = nl,
        breakeven_trades            = nb,
        win_rate_pct                = win_rate_pct,
        loss_rate_pct               = loss_rate_pct,
        sharpe_ratio                = sharpe_ratio,
        sortino_ratio               = sortino_ratio,
        calmar_ratio                = calmar_ratio,
        max_drawdown_abs            = mdd_abs,
        max_drawdown_pct            = mdd_pct,
        max_drawdown_duration_trades= mdd_dur,
        max_underwater_trades       = mdd_under,
        current_drawdown_pct        = current_dd_pct,
        all_drawdowns               = all_dds,
        profit_factor               = profit_factor,
        expectancy                  = expectancy,
        expectancy_r                = expectancy_r,
        recovery_factor             = recovery_factor,
        sqn                         = sqn,
        kelly_pct                   = kelly_pct,
        half_kelly_pct              = half_kelly_pct,
        avg_win                     = avg_win,
        avg_loss                    = avg_loss,
        avg_rr_achieved             = avg_rr_achieved,
        median_win                  = median_win,
        median_loss                 = median_loss,
        largest_win                 = largest_win,
        largest_loss                = largest_loss,
        pnl_std                     = pnl_std,
        avg_bars_in_trade           = avg_bars_in_trade,
        var_95                      = var_95,
        var_99                      = var_99,
        cvar_95                     = cvar_95,
        cvar_99                     = cvar_99,
        max_consecutive_wins        = max_cons_wins,
        max_consecutive_losses      = max_cons_losses,
        current_streak              = current_streak,
        by_strategy                 = by_strategy,
        by_regime                   = by_regime,
        by_scenario                 = by_scenario,
        monthly                     = monthly,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────────

def _compute_drawdowns(
    equity: List[float],
    trades: List[_Trade],
) -> Tuple[List[DrawdownPeriod], float, float, int, int]:
    """
    Enumerate all drawdown episodes on the trade-indexed equity curve.

    Returns (all_periods, max_dd_abs, max_dd_pct, max_dd_duration, max_underwater).
    equity[0] = initial capital; equity[i+1] = equity after trade i.
    """
    periods: List[DrawdownPeriod] = []

    peak_val = equity[0]
    peak_idx = 0
    in_dd    = False
    trough_val = equity[0]
    trough_idx = 0

    def _fmt_date(t: Optional[_Trade]) -> Optional[str]:
        if t is None:
            return None
        dt = t.exit_dt
        return dt.strftime("%Y-%m-%d") if dt else None

    n = len(trades)

    for i in range(1, len(equity)):
        v = equity[i]
        trade_idx = i - 1      # 0-based trade index

        if v >= peak_val:
            # Recovery or new peak
            if in_dd:
                dd_abs = peak_val - trough_val
                dd_pct = dd_abs / peak_val * 100
                periods.append(DrawdownPeriod(
                    drawdown_abs        = dd_abs,
                    drawdown_pct        = dd_pct,
                    peak_equity         = peak_val,
                    trough_equity       = trough_val,
                    peak_trade_idx      = peak_idx,
                    trough_trade_idx    = trough_idx,
                    recovery_trade_idx  = trade_idx,
                    peak_date           = _fmt_date(trades[peak_idx]   if peak_idx   < n else None),
                    trough_date         = _fmt_date(trades[trough_idx] if trough_idx < n else None),
                    recovery_date       = _fmt_date(trades[trade_idx]  if trade_idx  < n else None),
                    duration_trades     = trough_idx - peak_idx,
                    underwater_trades   = trade_idx  - peak_idx,
                ))
                in_dd = False
            peak_val = v
            peak_idx = trade_idx
            trough_val = v
            trough_idx = trade_idx
        else:
            if not in_dd:
                in_dd = True
            if v < trough_val:
                trough_val = v
                trough_idx = trade_idx

    # Close any open drawdown at end of sample
    if in_dd:
        dd_abs = peak_val - trough_val
        dd_pct = dd_abs / peak_val * 100
        periods.append(DrawdownPeriod(
            drawdown_abs        = dd_abs,
            drawdown_pct        = dd_pct,
            peak_equity         = peak_val,
            trough_equity       = trough_val,
            peak_trade_idx      = peak_idx,
            trough_trade_idx    = trough_idx,
            recovery_trade_idx  = None,
            peak_date           = _fmt_date(trades[peak_idx]   if peak_idx   < n else None),
            trough_date         = _fmt_date(trades[trough_idx] if trough_idx < n else None),
            recovery_date       = None,
            duration_trades     = trough_idx - peak_idx,
            underwater_trades   = n - peak_idx,
        ))

    if not periods:
        return [], 0.0, 0.0, 0, 0

    worst = max(periods, key=lambda d: d.drawdown_pct)
    return (
        sorted(periods, key=lambda d: -d.drawdown_pct),
        worst.drawdown_abs,
        worst.drawdown_pct,
        worst.duration_trades,
        worst.underwater_trades,
    )


def _compute_var_cvar(
    pnls: List[float],
) -> Tuple[float, float, float, float]:
    """
    Historical VaR and CVaR at 95% and 99% confidence.
    Returns (var_95, var_99, cvar_95, cvar_99) — all ≤ 0 when losses exist.
    """
    if not pnls:
        return 0.0, 0.0, 0.0, 0.0

    n   = len(pnls)
    srt = sorted(pnls)           # Ascending: worst losses first

    # VaR_α = (1-α)·N -th worst trade
    idx_95 = max(1, math.floor(0.05 * n))
    idx_99 = max(1, math.floor(0.01 * n))

    var_95 = srt[idx_95 - 1]
    var_99 = srt[idx_99 - 1]

    tail_95 = srt[:idx_95]
    tail_99 = srt[:idx_99]

    cvar_95 = statistics.mean(tail_95) if tail_95 else var_95
    cvar_99 = statistics.mean(tail_99) if tail_99 else var_99

    return var_95, var_99, cvar_95, cvar_99


def _compute_streaks(
    trades: List[_Trade],
) -> Tuple[int, int, int]:
    """
    Return (max_consecutive_wins, max_consecutive_losses, current_streak).
    current_streak: +k means k wins in a row at the end; -k means k losses.
    """
    max_w = max_l = cur = 0
    run_w = run_l = 0

    for t in trades:
        if t.is_win:
            run_w += 1
            run_l  = 0
            max_w  = max(max_w, run_w)
        elif t.pnl < 0:
            run_l += 1
            run_w  = 0
            max_l  = max(max_l, run_l)
        else:
            run_w = run_l = 0   # Breakeven resets streak

    if trades:
        last = trades[-1]
        if last.is_win:
            cur = run_w
        elif last.pnl < 0:
            cur = -run_l
        else:
            cur = 0

    return max_w, max_l, cur


def _breakdown(trades: List[_Trade], key: str) -> Dict[str, dict]:
    """Aggregate trades by a string attribute (strategy / regime / scenario)."""
    buckets: Dict[str, dict] = {}
    for t in trades:
        k = getattr(t, key, "unknown") or "unknown"
        if k not in buckets:
            buckets[k] = {"n": 0, "pnl": 0.0, "wins": 0, "r_vals": []}
        buckets[k]["n"]    += 1
        buckets[k]["pnl"]  += t.pnl
        if t.is_win:
            buckets[k]["wins"] += 1
        if t.r_multiple is not None:
            buckets[k]["r_vals"].append(t.r_multiple)

    result: Dict[str, dict] = {}
    for k, v in sorted(buckets.items(), key=lambda x: -x[1]["pnl"]):
        wr   = v["wins"] / v["n"] * 100 if v["n"] else 0.0
        r_av = statistics.mean(v["r_vals"]) if v["r_vals"] else None
        result[k] = {
            "n":       v["n"],
            "pnl":     round(v["pnl"], 2),
            "wr_pct":  round(wr, 1),
            "avg_rr":  round(r_av, 3) if r_av is not None else None,
        }
    return result


def _monthly_buckets(
    trades:          List[_Trade],
    initial_capital: float,
    equity:          List[float],
) -> List[MonthlyBucket]:
    """
    Group trades by exit month and compute per-month PnL, return, and win rate.
    equity_at_start_of_month is approximated as the equity level just before
    the first trade exiting in that month.
    """
    # Build {YYYY-MM: [trade_index, ...]}
    from collections import defaultdict
    month_map: Dict[str, List[int]] = defaultdict(list)
    for i, t in enumerate(trades):
        if t.exit_dt:
            key = t.exit_dt.strftime("%Y-%m")
            month_map[key].append(i)

    buckets: List[MonthlyBucket] = []
    for month_key in sorted(month_map):
        idxs  = month_map[month_key]
        first = idxs[0]
        # Equity before first trade of the month
        eq_start = equity[first]
        month_pnl = sum(trades[i].pnl for i in idxs)
        n_m   = len(idxs)
        wins  = sum(1 for i in idxs if trades[i].is_win)
        wr    = wins / n_m if n_m else 0.0
        ret   = month_pnl / eq_start * 100 if eq_start else 0.0
        buckets.append(MonthlyBucket(
            period     = month_key,
            pnl        = round(month_pnl, 2),
            return_pct = round(ret, 2),
            n_trades   = n_m,
            win_rate   = round(wr, 4),
        ))
    return buckets


def _empty_report(initial_capital: float) -> PerformanceReport:
    return PerformanceReport(
        initial_capital=initial_capital, final_equity=initial_capital,
        total_pnl=0.0, return_pct=0.0, cagr_pct=None,
        total_trades=0, winning_trades=0, losing_trades=0,
        breakeven_trades=0, win_rate_pct=0.0, loss_rate_pct=0.0,
        sharpe_ratio=None, sortino_ratio=None, calmar_ratio=None,
        max_drawdown_abs=0.0, max_drawdown_pct=0.0,
        max_drawdown_duration_trades=0, max_underwater_trades=0,
        current_drawdown_pct=0.0, all_drawdowns=[],
        profit_factor=0.0, expectancy=0.0, expectancy_r=None,
        recovery_factor=0.0, sqn=None, kelly_pct=None, half_kelly_pct=None,
        avg_win=0.0, avg_loss=0.0, avg_rr_achieved=0.0,
        median_win=0.0, median_loss=0.0, largest_win=0.0, largest_loss=0.0,
        pnl_std=0.0, avg_bars_in_trade=None,
        var_95=0.0, var_99=0.0, cvar_95=0.0, cvar_99=0.0,
        max_consecutive_wins=0, max_consecutive_losses=0, current_streak=0,
        by_strategy={}, by_regime={}, by_scenario={}, monthly=[],
    )


# ──────────────────────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────────────────────

def _fmt(v: Optional[float], fmt: str = ".2f", prefix: str = "") -> str:
    if v is None:
        return "—"
    return f"{prefix}{v:{fmt}}"


def _print_report(r: PerformanceReport, title: str, width: int) -> None:
    sep  = "═" * width
    sep2 = "─" * width
    def row(label: str, value: str) -> None:
        print(f"  {label:<36} {value}")

    print()
    print(sep)
    print(f"  {title}")
    print(sep)

    # ── Capital ───────────────────────────────────────────────────────────────
    print(f"\n  {'CAPITAL':}")
    print(sep2)
    row("Initial capital",       f"${r.initial_capital:>10,.2f}")
    row("Final equity",          f"${r.final_equity:>10,.2f}")
    row("Total PnL",             f"${r.total_pnl:>+10,.2f}")
    row("Return",                f"{r.return_pct:>+10.2f}%")
    row("CAGR",                  f"{_fmt(r.cagr_pct, '+.2f', '')}%" if r.cagr_pct is not None else "—")

    # ── Trades ────────────────────────────────────────────────────────────────
    print(f"\n  {'TRADES':}")
    print(sep2)
    row("Total",                 str(r.total_trades))
    row("Wins / Losses / BE",    f"{r.winning_trades} / {r.losing_trades} / {r.breakeven_trades}")
    row("Win rate",              f"{r.win_rate_pct:.1f}%")
    row("Avg win",               f"${r.avg_win:>+.2f}")
    row("Avg loss",              f"${r.avg_loss:>+.2f}")
    row("Median win / loss",     f"${r.median_win:>+.2f}  /  ${r.median_loss:>+.2f}")
    row("Largest win",           f"${r.largest_win:>+.2f}")
    row("Largest loss",          f"${r.largest_loss:>+.2f}")
    row("PnL std-dev",           f"${r.pnl_std:.2f}")
    row("Avg RR achieved",       f"{r.avg_rr_achieved:.2f}R")
    if r.avg_bars_in_trade is not None:
        row("Avg bars in trade", f"{r.avg_bars_in_trade:.1f}")

    # ── Risk-adjusted ─────────────────────────────────────────────────────────
    print(f"\n  {'RISK-ADJUSTED RETURNS':}")
    print(sep2)
    row("Sharpe ratio",          _fmt(r.sharpe_ratio,  ".3f"))
    row("Sortino ratio",         _fmt(r.sortino_ratio, ".3f"))
    row("Calmar ratio",          _fmt(r.calmar_ratio,  ".3f"))
    row("SQN (Van Tharp)",       _fmt(r.sqn,           ".3f"))
    row("Profit factor",         f"{r.profit_factor:.3f}")
    row("Expectancy / trade",    f"${r.expectancy:>+.2f}")
    row("Expectancy (R)",        _fmt(r.expectancy_r,  "+.3f") if r.expectancy_r is not None else "—")
    row("Recovery factor",       f"{r.recovery_factor:.2f}")

    # ── Kelly ─────────────────────────────────────────────────────────────────
    print(f"\n  {'POSITION SIZING':}")
    print(sep2)
    row("Kelly criterion",       _fmt(r.kelly_pct,      ".1f") + "%" if r.kelly_pct is not None else "—")
    row("Half-Kelly (recommended)", _fmt(r.half_kelly_pct, ".1f") + "%" if r.half_kelly_pct is not None else "—")

    # ── Drawdown ──────────────────────────────────────────────────────────────
    print(f"\n  {'DRAWDOWN':}")
    print(sep2)
    row("Max drawdown (abs)",    f"${r.max_drawdown_abs:.2f}")
    row("Max drawdown (%)",      f"{r.max_drawdown_pct:.2f}%")
    row("Max DD duration",       f"{r.max_drawdown_duration_trades} trades")
    row("Max underwater",        f"{r.max_underwater_trades} trades")
    row("Current drawdown",      f"{r.current_drawdown_pct:.2f}%")
    if r.all_drawdowns:
        print(f"\n  Top drawdown episodes:")
        print(f"  {'#':<3} {'DD%':>6}  {'DD$':>8}  {'Peak':>10}  {'Trough':>10}  {'Dur':>4}  {'UW':>4}")
        print(f"  {'-'*60}")
        for i, dd in enumerate(r.all_drawdowns[:5], 1):
            rec = dd.recovery_date or "open"
            print(f"  {i:<3} {dd.drawdown_pct:>5.1f}%  ${dd.drawdown_abs:>7.2f}"
                  f"  {dd.peak_date or '—':>10}  {dd.trough_date or '—':>10}"
                  f"  {dd.duration_trades:>4}  {dd.underwater_trades:>4}")

    # ── VaR / CVaR ────────────────────────────────────────────────────────────
    print(f"\n  {'RISK (HISTORICAL VaR/CVaR per trade)':}")
    print(sep2)
    row("VaR  95%",              f"${r.var_95:>+.2f}")
    row("VaR  99%",              f"${r.var_99:>+.2f}")
    row("CVaR 95% (Exp. Shortfall)", f"${r.cvar_95:>+.2f}")
    row("CVaR 99% (Exp. Shortfall)", f"${r.cvar_99:>+.2f}")

    # ── Streaks ───────────────────────────────────────────────────────────────
    print(f"\n  {'STREAKS':}")
    print(sep2)
    row("Max consecutive wins",  str(r.max_consecutive_wins))
    row("Max consecutive losses",str(r.max_consecutive_losses))
    streak_str = (f"+{r.current_streak} wins" if r.current_streak > 0
                  else f"{abs(r.current_streak)} losses" if r.current_streak < 0
                  else "breakeven")
    row("Current streak",        streak_str)

    # ── Strategy breakdown ────────────────────────────────────────────────────
    if r.by_strategy:
        print(f"\n  {'STRATEGY BREAKDOWN':}")
        print(sep2)
        print(f"  {'Strategy':<30} {'N':>4}  {'WR%':>5}  {'PnL':>9}  {'AvgRR':>7}")
        print(f"  {'-'*58}")
        for s, d in r.by_strategy.items():
            rr_s = f"{d['avg_rr']:.2f}R" if d["avg_rr"] is not None else "—"
            sign = "+" if d["pnl"] >= 0 else ""
            print(f"  {s:<30} {d['n']:>4}  {d['wr_pct']:>4.0f}%  {sign}${d['pnl']:>7,.2f}  {rr_s:>7}")

    # ── Regime breakdown ──────────────────────────────────────────────────────
    if r.by_regime and set(r.by_regime.keys()) != {"unknown"}:
        print(f"\n  {'REGIME BREAKDOWN':}")
        print(sep2)
        print(f"  {'Regime':<15} {'N':>4}  {'WR%':>5}  {'PnL':>9}")
        print(f"  {'-'*38}")
        for reg, d in r.by_regime.items():
            sign = "+" if d["pnl"] >= 0 else ""
            print(f"  {reg:<15} {d['n']:>4}  {d['wr_pct']:>4.0f}%  {sign}${d['pnl']:>7,.2f}")

    # ── Monthly returns ───────────────────────────────────────────────────────
    if r.monthly:
        print(f"\n  {'MONTHLY RETURNS':}")
        print(sep2)
        print(f"  {'Month':<8}  {'PnL':>8}  {'Ret%':>6}  {'N':>3}  {'WR%':>5}")
        print(f"  {'-'*40}")
        for m in r.monthly:
            sign = "+" if m.pnl >= 0 else ""
            print(f"  {m.period:<8}  {sign}${m.pnl:>6,.2f}  {m.return_pct:>+5.1f}%  {m.n_trades:>3}  {m.win_rate*100:>4.0f}%")

    print()
    print(sep)


def _to_dict(r: PerformanceReport) -> dict:
    """Flatten PerformanceReport to a JSON-serialisable dict."""
    return {
        "capital": {
            "initial":    r.initial_capital,
            "final":      r.final_equity,
            "total_pnl":  r.total_pnl,
            "return_pct": r.return_pct,
            "cagr_pct":   r.cagr_pct,
        },
        "trades": {
            "total":         r.total_trades,
            "wins":          r.winning_trades,
            "losses":        r.losing_trades,
            "breakeven":     r.breakeven_trades,
            "win_rate_pct":  r.win_rate_pct,
            "loss_rate_pct": r.loss_rate_pct,
        },
        "distribution": {
            "avg_win":        r.avg_win,
            "avg_loss":       r.avg_loss,
            "median_win":     r.median_win,
            "median_loss":    r.median_loss,
            "largest_win":    r.largest_win,
            "largest_loss":   r.largest_loss,
            "pnl_std":        r.pnl_std,
            "avg_rr":         r.avg_rr_achieved,
            "avg_bars":       r.avg_bars_in_trade,
        },
        "risk_adjusted": {
            "sharpe":         r.sharpe_ratio,
            "sortino":        r.sortino_ratio,
            "calmar":         r.calmar_ratio,
            "sqn":            r.sqn,
            "profit_factor":  r.profit_factor,
            "expectancy":     r.expectancy,
            "expectancy_r":   r.expectancy_r,
            "recovery_factor":r.recovery_factor,
        },
        "kelly": {
            "full_pct": r.kelly_pct,
            "half_pct": r.half_kelly_pct,
        },
        "drawdown": {
            "max_abs":      r.max_drawdown_abs,
            "max_pct":      r.max_drawdown_pct,
            "max_duration": r.max_drawdown_duration_trades,
            "max_uw":       r.max_underwater_trades,
            "current_pct":  r.current_drawdown_pct,
            "episodes": [
                {
                    "dd_pct":    d.drawdown_pct,
                    "dd_abs":    d.drawdown_abs,
                    "peak_date": d.peak_date,
                    "trough_date": d.trough_date,
                    "recovery_date": d.recovery_date,
                    "duration":  d.duration_trades,
                    "underwater": d.underwater_trades,
                }
                for d in r.all_drawdowns
            ],
        },
        "var_cvar": {
            "var_95":  r.var_95,
            "var_99":  r.var_99,
            "cvar_95": r.cvar_95,
            "cvar_99": r.cvar_99,
        },
        "streaks": {
            "max_wins":    r.max_consecutive_wins,
            "max_losses":  r.max_consecutive_losses,
            "current":     r.current_streak,
        },
        "by_strategy": r.by_strategy,
        "by_regime":   r.by_regime,
        "by_scenario": r.by_scenario,
        "monthly":     [
            {"period": m.period, "pnl": m.pnl, "return_pct": m.return_pct,
             "n_trades": m.n_trades, "win_rate": m.win_rate}
            for m in r.monthly
        ],
    }
