from __future__ import annotations
import plotly.graph_objects as go
from datetime import datetime


def add_line_overlay(
    fig: go.Figure,
    timestamps: list[datetime],
    values: list[float | None],
    name: str = "Indicator",
    color: str = "#ffd700",
    width: float = 1.5,
    row: int = 1,
    col: int = 1,
    dash: str | None = None,
) -> go.Figure:
    """Add a line overlay (e.g., SMA, EMA) on the price chart."""
    # Filter None values
    clean_ts = []
    clean_vals = []
    for t, v in zip(timestamps, values):
        if v is not None:
            clean_ts.append(t)
            clean_vals.append(v)

    fig.add_trace(
        go.Scatter(
            x=clean_ts,
            y=clean_vals,
            name=name,
            line=dict(color=color, width=width, dash=dash),
            mode="lines",
        ),
        row=row, col=col,
    )
    return fig


def add_band_overlay(
    fig: go.Figure,
    timestamps: list[datetime],
    upper: list[float],
    lower: list[float],
    name: str = "Band",
    color: str = "rgba(255, 215, 0, 0.1)",
    row: int = 1,
    col: int = 1,
) -> go.Figure:
    """Add a band overlay (e.g., Bollinger Bands)."""
    fig.add_trace(
        go.Scatter(
            x=timestamps, y=upper, name=f"{name} Upper",
            line=dict(width=0), mode="lines", showlegend=False,
        ),
        row=row, col=col,
    )
    fig.add_trace(
        go.Scatter(
            x=timestamps, y=lower, name=f"{name} Lower",
            line=dict(width=0), mode="lines",
            fill="tonexty", fillcolor=color, showlegend=False,
        ),
        row=row, col=col,
    )
    return fig
