from __future__ import annotations
import plotly.graph_objects as go
from btframework.charts.panels import create_multi_panel, apply_dark_theme
from btframework.charts.candlestick import create_candlestick, create_volume_bars
from btframework.charts.markers import add_trade_markers
from btframework.charts.equity import add_equity_curve, add_drawdown
from btframework.charts.overlays import add_line_overlay


class ChartEngine:
    """Assembles and renders interactive TradingView-like charts."""

    def __init__(self, result=None):
        self._result = result
        self._fig: go.Figure | None = None

    def build(self) -> go.Figure:
        """Build the full chart from backtest results."""
        result = self._result
        if result is None:
            raise ValueError("No backtest result provided")

        equity_curve = result.equity_curve
        trades = result.trades
        ohlcv_data = getattr(result, "ohlcv_data", {})
        indicators = getattr(result, "indicators", None)

        # Determine panels: Price+Volume+Equity (+ extra indicator panels if needed)
        num_panels = 3
        fig = create_multi_panel(num_panels=num_panels)

        # === Panel 1: Candlestick + Overlays ===
        if ohlcv_data:
            # Use first symbol's data
            symbol = next(iter(ohlcv_data))
            bars = ohlcv_data[symbol]

            timestamps = [b["timestamp"] for b in bars]
            opens = [b["open"] for b in bars]
            highs = [b["high"] for b in bars]
            lows = [b["low"] for b in bars]
            closes = [b["close"] for b in bars]
            volumes = [b["volume"] for b in bars]

            # Candlestick
            candle = create_candlestick(timestamps, opens, highs, lows, closes, name=symbol)
            fig.add_trace(candle, row=1, col=1)

            # === Panel 2: Volume ===
            vol_bars = create_volume_bars(timestamps, volumes, closes, opens)
            fig.add_trace(vol_bars, row=2, col=1)

            # Indicator overlays on price panel
            if indicators:
                colors = ["#ffd700", "#ff69b4", "#00bfff", "#ff8c00", "#7fff00", "#da70d6"]
                color_idx = 0
                for name, ind in indicators.indicators.items():
                    series = ind.series
                    if not series:
                        continue

                    # Check if indicator values are in price range (overlay) vs separate panel
                    non_none = [v for v in series if v is not None and not isinstance(v, tuple)]
                    if not non_none:
                        continue

                    avg_val = sum(non_none) / len(non_none)
                    avg_price = sum(closes) / len(closes)

                    # If values are in same order of magnitude as price -> overlay on price
                    # Otherwise it's an oscillator (like RSI) -> skip for now (could add panel)
                    ratio = avg_val / avg_price if avg_price else 0
                    if 0.5 < ratio < 2.0:
                        # Price overlay (EMA, SMA, etc.)
                        # Align series to timestamps (series may be shorter)
                        ind_timestamps = timestamps[-len(series):]
                        ind_values = series
                        color = colors[color_idx % len(colors)]
                        color_idx += 1
                        add_line_overlay(fig, ind_timestamps, ind_values, name=name,
                                         color=color, row=1, col=1)

        # Trade markers
        if trades:
            add_trade_markers(fig, trades, row=1, col=1)

        # === Panel 3: Equity + Drawdown ===
        if equity_curve:
            add_equity_curve(fig, equity_curve, row=3, col=1)
            add_drawdown(fig, equity_curve, row=3, col=1)

        # Apply theme
        title = result.config.name if result.config else "Backtest Results"
        apply_dark_theme(fig, title=title)

        # Labels
        fig.update_yaxes(title_text="Price", row=1, col=1)
        fig.update_yaxes(title_text="Volume", row=2, col=1)
        fig.update_yaxes(title_text="Equity / DD%", row=3, col=1)

        self._fig = fig
        return fig

    def show(self, **kwargs) -> None:
        """Display chart in browser."""
        if self._fig is None:
            self.build()
        self._fig.show(**kwargs)

    def save(self, path: str, **kwargs) -> None:
        """Save chart as HTML."""
        if self._fig is None:
            self.build()
        self._fig.write_html(path, **kwargs)

    def add_indicator_overlay(
        self,
        timestamps,
        values,
        name: str = "Indicator",
        panel: int = 1,
        **kwargs,
    ) -> None:
        """Add an indicator overlay to the chart."""
        if self._fig is None:
            self.build()
        add_line_overlay(self._fig, timestamps, values, name=name, row=panel, col=1, **kwargs)
