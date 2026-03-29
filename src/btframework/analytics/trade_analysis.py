from __future__ import annotations
from datetime import timedelta
import numpy as np


def analyze_trades(trades: list[dict]) -> dict:
    """Detailed trade-level analysis."""
    if not trades:
        return {}

    pnls = [float(t["pnl"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # Duration analysis
    durations = []
    for t in trades:
        if "opened_at" in t and "closed_at" in t and t["opened_at"] and t["closed_at"]:
            dur = t["closed_at"] - t["opened_at"]
            durations.append(dur.total_seconds() / 3600)  # hours

    return {
        "total_trades": len(trades),
        "winning": len(wins),
        "losing": len(losses),
        "total_pnl": sum(pnls),
        "avg_pnl": float(np.mean(pnls)),
        "median_pnl": float(np.median(pnls)),
        "std_pnl": float(np.std(pnls)) if len(pnls) > 1 else 0.0,
        "best_trade": max(pnls),
        "worst_trade": min(pnls),
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        "largest_win": max(wins) if wins else 0.0,
        "largest_loss": min(losses) if losses else 0.0,
        "avg_duration_hours": float(np.mean(durations)) if durations else 0.0,
        "max_duration_hours": max(durations) if durations else 0.0,
        "min_duration_hours": min(durations) if durations else 0.0,
    }
