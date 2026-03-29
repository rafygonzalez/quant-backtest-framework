from __future__ import annotations
from pathlib import Path
from datetime import datetime


def generate_tearsheet(result, path: str | Path) -> None:
    """Generate an HTML tearsheet report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    from btframework.analytics.metrics import compute_all_metrics
    from btframework.analytics.trade_analysis import analyze_trades

    metrics = compute_all_metrics(result.trades, result.equity_curve)
    trade_stats = analyze_trades(result.trades)

    html = _build_html(metrics, trade_stats, result)

    with open(path, "w") as f:
        f.write(html)


def _build_html(metrics: dict, trade_stats: dict, result) -> str:
    """Build HTML report string."""
    title = result.config.name if result.config else "Backtest Report"

    # Format metrics for display
    metrics_rows = ""
    display_metrics = {
        "Total Trades": metrics.get("total_trades", 0),
        "Win Rate": f"{metrics.get('win_rate', 0):.1%}",
        "Profit Factor": f"{metrics.get('profit_factor', 0):.2f}",
        "Sharpe Ratio": f"{metrics.get('sharpe', 0):.2f}",
        "Sortino Ratio": f"{metrics.get('sortino', 0):.2f}",
        "Calmar Ratio": f"{metrics.get('calmar', 0):.2f}",
        "Max Drawdown": f"{metrics.get('max_drawdown_pct', 0):.2%}",
        "Expectancy": f"${metrics.get('expectancy', 0):.2f}",
        "Risk/Reward": f"{metrics.get('risk_reward', 0):.2f}",
        "Max Consecutive Wins": metrics.get("max_consecutive_wins", 0),
        "Max Consecutive Losses": metrics.get("max_consecutive_losses", 0),
    }

    for label, value in display_metrics.items():
        metrics_rows += f"<tr><td>{label}</td><td>{value}</td></tr>\n"

    # Trade stats rows
    trade_rows = ""
    if trade_stats:
        for label, value in trade_stats.items():
            if isinstance(value, float):
                trade_rows += f"<tr><td>{label}</td><td>{value:.2f}</td></tr>\n"
            else:
                trade_rows += f"<tr><td>{label}</td><td>{value}</td></tr>\n"

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #1a1a2e; color: #e0e0e0; margin: 40px; }}
        h1 {{ color: #00d4aa; }}
        h2 {{ color: #4ecdc4; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; max-width: 600px; margin: 10px 0; }}
        th, td {{ padding: 10px 16px; text-align: left; border-bottom: 1px solid #333; }}
        th {{ background: #16213e; color: #00d4aa; }}
        tr:hover {{ background: #16213e; }}
        .summary {{ display: flex; gap: 40px; flex-wrap: wrap; }}
        .card {{ background: #16213e; padding: 20px; border-radius: 8px; min-width: 250px; }}
        .card h3 {{ color: #00d4aa; margin-top: 0; }}
        .card .value {{ font-size: 24px; font-weight: bold; }}
        .positive {{ color: #00d4aa; }}
        .negative {{ color: #ff6b6b; }}
        .footer {{ margin-top: 40px; color: #666; font-size: 12px; }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

    <div class="summary">
        <div class="card">
            <h3>Final Equity</h3>
            <div class="value">${float(result.account.equity):,.2f}</div>
        </div>
        <div class="card">
            <h3>Total Return</h3>
            <div class="value {'positive' if result.account.equity > result.account.initial_balance else 'negative'}">
                {float((result.account.equity - result.account.initial_balance) / result.account.initial_balance * 100):.2f}%
            </div>
        </div>
        <div class="card">
            <h3>Total Trades</h3>
            <div class="value">{metrics.get('total_trades', 0)}</div>
        </div>
    </div>

    <h2>Performance Metrics</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        {metrics_rows}
    </table>

    <h2>Trade Analysis</h2>
    <table>
        <tr><th>Metric</th><th>Value</th></tr>
        {trade_rows}
    </table>

    <div class="footer">
        <p>BTFramework Tearsheet Report</p>
    </div>
</body>
</html>"""
