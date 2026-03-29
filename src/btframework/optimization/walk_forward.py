"""Walk-forward optimization.

Splits data into rolling in-sample / out-of-sample windows,
optimizes parameters on IS, evaluates on OOS, and computes
an overfitting score.
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from btframework.core.engine import BacktestEngine, BacktestResult
from btframework.data.feed import DataFeed, SlicedFeed
from btframework.events.market import MarketDataEvent

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardWindow:
    """Results for a single walk-forward window."""
    window_index: int
    is_start: datetime
    is_end: datetime
    oos_start: datetime
    oos_end: datetime
    best_params: dict = field(default_factory=dict)
    is_result: BacktestResult | None = None
    oos_result: BacktestResult | None = None
    is_metric: float = 0.0
    oos_metric: float = 0.0


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward optimization results."""
    windows: list[WalkForwardWindow] = field(default_factory=list)
    aggregate_oos_metric: float = 0.0
    overfitting_score: float = 0.0

    @property
    def is_overfit(self) -> bool:
        """Consider overfit if OOS performance is less than 50% of IS."""
        return self.overfitting_score < 0.5

    def summary(self) -> dict:
        return {
            "num_windows": len(self.windows),
            "aggregate_oos_metric": self.aggregate_oos_metric,
            "overfitting_score": self.overfitting_score,
            "is_overfit": self.is_overfit,
            "windows": [
                {
                    "index": w.window_index,
                    "best_params": w.best_params,
                    "is_metric": w.is_metric,
                    "oos_metric": w.oos_metric,
                }
                for w in self.windows
            ],
        }


class WalkForwardOptimizer:
    """Rolling walk-forward optimizer with grid search.

    Usage:
        optimizer = WalkForwardOptimizer(
            engine_factory=lambda: BacktestEngine().with_account(account),
            param_space={"fast": [5, 10, 15], "slow": [20, 30, 50]},
            strategy_cls=MyCrossoverStrategy,
            objective=lambda r: r.summary()["return_pct"],
        )
        result = optimizer.run(feed, is_bars=252, oos_bars=63, step_bars=63)
    """

    def __init__(
        self,
        engine_factory: Callable[[], BacktestEngine],
        param_space: dict[str, list],
        strategy_cls: type,
        objective: Callable[[BacktestResult], float],
        maximize: bool = True,
    ):
        self._engine_factory = engine_factory
        self._param_space = param_space
        self._strategy_cls = strategy_cls
        self._objective = objective
        self._maximize = maximize

    def run(
        self,
        feed: DataFeed,
        is_bars: int = 252,
        oos_bars: int = 63,
        step_bars: int = 63,
    ) -> WalkForwardResult:
        """Execute walk-forward optimization.

        1. Materialize the feed into a list of bar dicts.
        2. Generate rolling windows.
        3. For each window: grid search on IS, evaluate best on OOS.
        4. Aggregate OOS metrics and compute overfitting score.
        """
        # Materialize all bars
        all_bars = list(feed)
        total = len(all_bars)

        if total < is_bars + oos_bars:
            raise ValueError(
                f"Not enough bars ({total}) for IS ({is_bars}) + OOS ({oos_bars})"
            )

        # Extract symbols from first bar
        symbols = list(all_bars[0].keys()) if all_bars else []

        # Generate windows
        windows: list[WalkForwardWindow] = []
        start = 0
        window_idx = 0

        while start + is_bars + oos_bars <= total:
            is_start_idx = start
            is_end_idx = start + is_bars
            oos_start_idx = is_end_idx
            oos_end_idx = oos_start_idx + oos_bars

            # Get timestamps for reporting
            is_start_ts = self._get_timestamp(all_bars[is_start_idx])
            is_end_ts = self._get_timestamp(all_bars[is_end_idx - 1])
            oos_start_ts = self._get_timestamp(all_bars[oos_start_idx])
            oos_end_ts = self._get_timestamp(all_bars[oos_end_idx - 1])

            window = WalkForwardWindow(
                window_index=window_idx,
                is_start=is_start_ts,
                is_end=is_end_ts,
                oos_start=oos_start_ts,
                oos_end=oos_end_ts,
            )

            # Grid search on IS
            is_feed = SlicedFeed(all_bars, is_start_idx, is_end_idx, symbols)
            best_params, best_metric, best_result = self._grid_search(is_feed)

            window.best_params = best_params
            window.is_metric = best_metric
            window.is_result = best_result

            # Evaluate best params on OOS
            oos_feed = SlicedFeed(all_bars, oos_start_idx, oos_end_idx, symbols)
            oos_result = self._run_single(oos_feed, best_params)
            oos_metric = self._objective(oos_result)

            window.oos_metric = oos_metric
            window.oos_result = oos_result

            windows.append(window)
            logger.info(
                f"Window {window_idx}: IS={best_metric:.4f} OOS={oos_metric:.4f} "
                f"params={best_params}"
            )

            start += step_bars
            window_idx += 1

        # Aggregate results
        if not windows:
            return WalkForwardResult()

        is_metrics = [w.is_metric for w in windows]
        oos_metrics = [w.oos_metric for w in windows]

        avg_is = sum(is_metrics) / len(is_metrics) if is_metrics else 0
        avg_oos = sum(oos_metrics) / len(oos_metrics) if oos_metrics else 0

        overfitting_score = avg_oos / avg_is if avg_is != 0 else 0.0

        return WalkForwardResult(
            windows=windows,
            aggregate_oos_metric=avg_oos,
            overfitting_score=overfitting_score,
        )

    def _grid_search(
        self, feed: SlicedFeed
    ) -> tuple[dict, float, BacktestResult]:
        """Exhaustive grid search over parameter space."""
        param_names = list(self._param_space.keys())
        param_values = list(self._param_space.values())

        best_params: dict = {}
        best_metric = float("-inf") if self._maximize else float("inf")
        best_result: BacktestResult | None = None

        for combo in itertools.product(*param_values):
            params = dict(zip(param_names, combo))
            result = self._run_single(feed, params)
            metric = self._objective(result)

            is_better = (
                metric > best_metric if self._maximize else metric < best_metric
            )
            if is_better:
                best_metric = metric
                best_params = params
                best_result = result

        return best_params, best_metric, best_result  # type: ignore[return-value]

    def _run_single(self, feed: SlicedFeed, params: dict) -> BacktestResult:
        """Run a single backtest with given parameters."""
        engine = self._engine_factory()
        engine.with_feed(feed).with_strategy(self._strategy_cls, params)
        return engine.run()

    @staticmethod
    def _get_timestamp(bar_dict: dict[str, MarketDataEvent]) -> datetime:
        """Extract timestamp from a bar event dict."""
        event = next(iter(bar_dict.values()))
        return event.bar_timestamp or event.timestamp
