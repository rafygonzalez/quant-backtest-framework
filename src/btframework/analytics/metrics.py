from __future__ import annotations
import math
from decimal import Decimal
from datetime import datetime
import numpy as np


def sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0, periods: int = 252) -> float:
    """Annualized Sharpe ratio."""
    if not returns or len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    excess = arr - risk_free_rate / periods
    std = np.std(excess, ddof=1)
    if std == 0:
        return 0.0
    return float(np.mean(excess) / std * np.sqrt(periods))


def sortino_ratio(returns: list[float], risk_free_rate: float = 0.0, periods: int = 252) -> float:
    """Annualized Sortino ratio (downside deviation only)."""
    if not returns or len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    excess = arr - risk_free_rate / periods
    downside = arr[arr < 0]
    if len(downside) == 0:
        return float("inf")
    downside_std = np.std(downside, ddof=1)
    if downside_std == 0:
        return float("inf")
    return float(np.mean(excess) / downside_std * np.sqrt(periods))


def calmar_ratio(returns: list[float], max_dd: float, periods: int = 252) -> float:
    """Calmar ratio = annualized return / max drawdown."""
    if max_dd == 0 or not returns:
        return 0.0
    annual_return = np.mean(returns) * periods
    return float(annual_return / abs(max_dd))


def max_drawdown(equity_curve: list[tuple[datetime, Decimal | float]]) -> dict:
    """Calculate maximum drawdown with duration."""
    if not equity_curve:
        return {"max_dd": 0.0, "max_dd_pct": 0.0, "max_dd_duration_bars": 0, "peak_date": None, "trough_date": None}

    values = [float(v) for _, v in equity_curve]
    peak = values[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    peak_idx = 0
    trough_idx = 0
    current_peak_idx = 0

    for i, v in enumerate(values):
        if v > peak:
            peak = v
            current_peak_idx = i
        dd = peak - v
        dd_pct = dd / peak if peak > 0 else 0.0
        if dd_pct > max_dd_pct:
            max_dd = dd
            max_dd_pct = dd_pct
            peak_idx = current_peak_idx
            trough_idx = i

    return {
        "max_dd": max_dd,
        "max_dd_pct": max_dd_pct,
        "max_dd_duration_bars": trough_idx - peak_idx,
        "peak_date": equity_curve[peak_idx][0] if peak_idx < len(equity_curve) else None,
        "trough_date": equity_curve[trough_idx][0] if trough_idx < len(equity_curve) else None,
    }


def profit_factor(trades: list[dict]) -> float:
    """Gross profit / Gross loss."""
    gross_profit = sum(float(t["pnl"]) for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(float(t["pnl"]) for t in trades if t["pnl"] <= 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def expectancy(trades: list[dict]) -> float:
    """Expected value per trade."""
    if not trades:
        return 0.0
    wins = [float(t["pnl"]) for t in trades if t["pnl"] > 0]
    losses = [float(t["pnl"]) for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades)
    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 0
    return float(win_rate * avg_win - (1 - win_rate) * avg_loss)


def win_rate(trades: list[dict]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return wins / len(trades)


def risk_reward_ratio(trades: list[dict]) -> float:
    """Average win / Average loss."""
    wins = [float(t["pnl"]) for t in trades if t["pnl"] > 0]
    losses = [abs(float(t["pnl"])) for t in trades if t["pnl"] <= 0]
    if not wins or not losses:
        return 0.0
    return float(np.mean(wins) / np.mean(losses))


def max_consecutive(trades: list[dict], win: bool = True) -> int:
    """Max consecutive wins or losses."""
    max_count = 0
    current = 0
    for t in trades:
        is_win = t["pnl"] > 0
        if is_win == win:
            current += 1
            max_count = max(max_count, current)
        else:
            current = 0
    return max_count


def monthly_returns(equity_curve: list[tuple[datetime, Decimal | float]]) -> dict[str, float]:
    """Monthly returns as {YYYY-MM: return_pct}."""
    if len(equity_curve) < 2:
        return {}
    result = {}
    prev_month = None
    month_start_val = None
    for dt, val in equity_curve:
        month_key = dt.strftime("%Y-%m")
        if month_key != prev_month:
            if prev_month and month_start_val:
                ret = (float(prev_val) - float(month_start_val)) / float(month_start_val) * 100
                result[prev_month] = ret
            month_start_val = val
            prev_month = month_key
        prev_val = val
    # Last month
    if prev_month and month_start_val:
        ret = (float(prev_val) - float(month_start_val)) / float(month_start_val) * 100
        result[prev_month] = ret
    return result


def compute_all_metrics(trades: list[dict], equity_curve: list[tuple[datetime, Decimal | float]]) -> dict:
    """Compute all available metrics."""
    # Daily returns from equity curve
    returns = []
    for i in range(1, len(equity_curve)):
        prev = float(equity_curve[i-1][1])
        curr = float(equity_curve[i][1])
        if prev > 0:
            returns.append((curr - prev) / prev)

    dd = max_drawdown(equity_curve)

    return {
        "total_trades": len(trades),
        "win_rate": win_rate(trades),
        "risk_reward": risk_reward_ratio(trades),
        "profit_factor": profit_factor(trades),
        "expectancy": expectancy(trades),
        "sharpe": sharpe_ratio(returns),
        "sortino": sortino_ratio(returns),
        "calmar": calmar_ratio(returns, dd["max_dd_pct"]),
        "max_drawdown": dd["max_dd"],
        "max_drawdown_pct": dd["max_dd_pct"],
        "max_drawdown_duration": dd["max_dd_duration_bars"],
        "max_consecutive_wins": max_consecutive(trades, win=True),
        "max_consecutive_losses": max_consecutive(trades, win=False),
        "monthly_returns": monthly_returns(equity_curve),
    }
