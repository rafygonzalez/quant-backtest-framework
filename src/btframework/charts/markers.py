from __future__ import annotations
import plotly.graph_objects as go
from datetime import datetime


def add_trade_markers(
    fig: go.Figure,
    trades: list[dict],
    row: int = 1,
    col: int = 1,
) -> go.Figure:
    """Add entry/exit markers for trades on the price chart."""
    # Entries
    entry_dates = []
    entry_prices = []
    entry_texts = []
    entry_colors = []

    # Exits
    exit_dates = []
    exit_prices = []
    exit_texts = []
    exit_colors = []

    for t in trades:
        # Entry marker
        if "opened_at" in t and t["opened_at"]:
            entry_dates.append(t["opened_at"])
            entry_prices.append(float(t["entry_price"]))
            side = t.get("side", "LONG")
            entry_texts.append(f"{side} Entry<br>Price: {t['entry_price']}<br>Qty: {t.get('quantity', '')}")
            entry_colors.append("#00d4aa" if side == "LONG" else "#ff6b6b")

        # Exit marker
        if "closed_at" in t and t["closed_at"]:
            exit_dates.append(t["closed_at"])
            exit_prices.append(float(t["exit_price"]))
            pnl = float(t.get("pnl", 0))
            exit_texts.append(f"Exit<br>Price: {t['exit_price']}<br>PnL: ${pnl:.2f}")
            exit_colors.append("#00d4aa" if pnl >= 0 else "#ff6b6b")

    # Entry markers (triangle-up for long, triangle-down for short)
    if entry_dates:
        fig.add_trace(
            go.Scatter(
                x=entry_dates,
                y=entry_prices,
                mode="markers",
                name="Entry",
                marker=dict(
                    symbol="triangle-up",
                    size=12,
                    color=entry_colors,
                    line=dict(width=1, color="white"),
                ),
                text=entry_texts,
                hoverinfo="text",
            ),
            row=row, col=col,
        )

    # Exit markers (x)
    if exit_dates:
        fig.add_trace(
            go.Scatter(
                x=exit_dates,
                y=exit_prices,
                mode="markers",
                name="Exit",
                marker=dict(
                    symbol="x",
                    size=10,
                    color=exit_colors,
                    line=dict(width=2),
                ),
                text=exit_texts,
                hoverinfo="text",
            ),
            row=row, col=col,
        )

    return fig
