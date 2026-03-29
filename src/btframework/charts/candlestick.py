from __future__ import annotations
import plotly.graph_objects as go
from datetime import datetime
from decimal import Decimal


def create_candlestick(
    timestamps: list[datetime],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float] | None = None,
    name: str = "",
) -> go.Candlestick:
    """Create a candlestick trace."""
    return go.Candlestick(
        x=timestamps,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
        name=name or "Price",
        increasing_line_color="#00d4aa",
        decreasing_line_color="#ff6b6b",
        increasing_fillcolor="#00d4aa",
        decreasing_fillcolor="#ff6b6b",
    )


def create_volume_bars(
    timestamps: list[datetime],
    volumes: list[float],
    closes: list[float],
    opens: list[float],
) -> go.Bar:
    """Create volume bar trace with color based on price direction."""
    colors = [
        "#00d4aa" if c >= o else "#ff6b6b"
        for c, o in zip(closes, opens)
    ]
    return go.Bar(
        x=timestamps,
        y=volumes,
        name="Volume",
        marker_color=colors,
        opacity=0.5,
    )
