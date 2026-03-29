from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def run_chart_server(result, port: int = 8050, debug: bool = False) -> None:
    """Run interactive Dash server for exploring backtest results."""
    try:
        import dash
        from dash import html, dcc
        import dash_bootstrap_components as dbc
        from dash.dependencies import Input, Output
    except ImportError:
        logger.error("Dash not installed. Run: pip install dash dash-bootstrap-components")
        raise

    from btframework.charts.engine import ChartEngine
    from btframework.analytics.metrics import compute_all_metrics
    from btframework.analytics.trade_analysis import analyze_trades

    app = dash.Dash(
        __name__,
        external_stylesheets=[dbc.themes.DARKLY],
    )

    # Build chart
    chart_engine = ChartEngine(result)
    fig = chart_engine.build()

    # Compute metrics
    metrics = compute_all_metrics(result.trades, result.equity_curve)
    trade_stats = analyze_trades(result.trades)

    # Layout
    app.layout = dbc.Container([
        dbc.Row([
            dbc.Col(html.H1("Backtest Dashboard", className="text-center my-3"), width=12),
        ]),

        # Summary cards
        dbc.Row([
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.H5("Final Equity", className="card-title"),
                    html.H3(f"${float(result.account.equity):,.2f}"),
                ]),
            ]), width=3),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.H5("Total Return", className="card-title"),
                    html.H3(f"{float((result.account.equity - result.account.initial_balance) / result.account.initial_balance * 100):.2f}%"),
                ]),
            ]), width=3),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.H5("Sharpe Ratio", className="card-title"),
                    html.H3(f"{metrics.get('sharpe', 0):.2f}"),
                ]),
            ]), width=3),
            dbc.Col(dbc.Card([
                dbc.CardBody([
                    html.H5("Max Drawdown", className="card-title"),
                    html.H3(f"{metrics.get('max_drawdown_pct', 0):.2%}"),
                ]),
            ]), width=3),
        ], className="mb-3"),

        # Main chart
        dbc.Row([
            dbc.Col(dcc.Graph(figure=fig, style={"height": "600px"}), width=12),
        ]),

        # Trades table
        dbc.Row([
            dbc.Col([
                html.H4("Trade Log"),
                _build_trades_table(result.trades),
            ], width=12),
        ], className="mt-3"),
    ], fluid=True)

    logger.info(f"Starting chart server on port {port}")
    app.run(debug=debug, port=port)


def _build_trades_table(trades: list[dict]):
    """Build an HTML table of trades."""
    from dash import html

    if not trades:
        return html.P("No trades")

    headers = ["Symbol", "Side", "Entry", "Exit", "Qty", "PnL"]
    header_row = html.Tr([html.Th(h) for h in headers])

    rows = []
    for t in trades[-50:]:  # Last 50
        pnl = float(t.get("pnl", 0))
        color = "#00d4aa" if pnl >= 0 else "#ff6b6b"
        rows.append(html.Tr([
            html.Td(t.get("symbol", "")),
            html.Td(t.get("side", "")),
            html.Td(str(t.get("entry_price", ""))),
            html.Td(str(t.get("exit_price", ""))),
            html.Td(str(t.get("quantity", ""))),
            html.Td(f"${pnl:.2f}", style={"color": color}),
        ]))

    return html.Table(
        [html.Thead(header_row), html.Tbody(rows)],
        className="table table-dark table-striped",
    )
