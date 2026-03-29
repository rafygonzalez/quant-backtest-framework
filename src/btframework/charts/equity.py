from __future__ import annotations
import plotly.graph_objects as go
from datetime import datetime
from decimal import Decimal


def add_equity_curve(
    fig: go.Figure,
    equity_curve: list[tuple[datetime, Decimal | float]],
    row: int = 1,
    col: int = 1,
) -> go.Figure:
    """Add equity curve line."""
    dates = [dt for dt, _ in equity_curve]
    values = [float(v) for _, v in equity_curve]

    fig.add_trace(
        go.Scatter(
            x=dates,
            y=values,
            name="Equity",
            line=dict(color="#00d4aa", width=2),
            fill="tozeroy",
            fillcolor="rgba(0, 212, 170, 0.1)",
        ),
        row=row, col=col,
    )
    return fig


def add_drawdown(
    fig: go.Figure,
    equity_curve: list[tuple[datetime, Decimal | float]],
    row: int = 1,
    col: int = 1,
) -> go.Figure:
    """Add drawdown chart."""
    dates = [dt for dt, _ in equity_curve]
    values = [float(v) for _, v in equity_curve]

    # Calculate drawdown
    peak = values[0] if values else 0
    drawdowns = []
    for v in values:
        if v > peak:
            peak = v
        dd_pct = (v - peak) / peak * 100 if peak > 0 else 0
        drawdowns.append(dd_pct)

    fig.add_trace(
        go.Scatter(
            x=dates,
            y=drawdowns,
            name="Drawdown %",
            line=dict(color="#ff6b6b", width=1.5),
            fill="tozeroy",
            fillcolor="rgba(255, 107, 107, 0.2)",
        ),
        row=row, col=col,
    )
    return fig
