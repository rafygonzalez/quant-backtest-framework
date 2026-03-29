from __future__ import annotations
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def create_multi_panel(
    num_panels: int = 3,
    panel_heights: list[float] | None = None,
    shared_x: bool = True,
) -> go.Figure:
    """Create multi-panel layout (price, volume, indicators)."""
    if panel_heights is None:
        # Default: price gets most space, volume and indicator panels smaller
        if num_panels == 1:
            panel_heights = [1.0]
        elif num_panels == 2:
            panel_heights = [0.7, 0.3]
        elif num_panels == 3:
            panel_heights = [0.5, 0.2, 0.3]
        else:
            price_height = 0.4
            other_height = 0.6 / (num_panels - 1)
            panel_heights = [price_height] + [other_height] * (num_panels - 1)

    fig = make_subplots(
        rows=num_panels,
        cols=1,
        shared_xaxes=shared_x,
        vertical_spacing=0.03,
        row_heights=panel_heights,
    )

    return fig


def apply_dark_theme(fig: go.Figure, title: str = "") -> go.Figure:
    """Apply TradingView-like dark theme."""
    fig.update_layout(
        title=title,
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0", family="Consolas, monospace"),
        xaxis_rangeslider_visible=False,
        legend=dict(
            bgcolor="rgba(22, 33, 62, 0.8)",
            bordercolor="#333",
            borderwidth=1,
        ),
        margin=dict(l=60, r=20, t=40, b=20),
        hovermode="x unified",
    )

    # Style all axes
    axis_style = dict(
        gridcolor="#2a2a4a",
        zerolinecolor="#333",
        showgrid=True,
    )
    fig.update_xaxes(**axis_style)
    fig.update_yaxes(**axis_style)

    return fig
