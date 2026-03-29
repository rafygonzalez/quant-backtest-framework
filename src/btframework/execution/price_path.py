"""Intra-bar price path generation for realistic order execution.

Generates synthetic tick-level price paths from OHLC bars so that
stop/limit order triggers can be evaluated in a meaningful order.
"""
from __future__ import annotations

import random as _random
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from btframework.data.models import Bar


@dataclass(frozen=True)
class PriceTick:
    """A single synthetic price point within a bar."""
    minute_offset: int  # 0 to num_points-1
    price: Decimal


class PricePathGenerator(Protocol):
    """Protocol for generating intra-bar price paths."""

    def generate(self, bar: Bar, num_points: int = 60) -> list[PriceTick]:
        ...


class OHLCPricePathGenerator:
    """Generates a price path that respects OHLC constraints.

    Algorithm:
    1. Decide if high or low comes first based on bar direction:
       - Bullish (close > open): 70% probability low comes first
       - Bearish (close < open): 70% probability high comes first
    2. Place anchors: open@0, first extreme@1/3, second extreme@2/3, close@end
    3. Interpolate linearly between anchors with clamped gaussian noise
    4. Guarantee exact high and low appear in the path
    """

    MIN_POINTS = 4
    DIRECTIONAL_BIAS = 0.7          # Probability that the favored extreme comes first
    ANCHOR_FIRST_FRACTION = 1 / 3   # First extreme at 1/3 of bar
    ANCHOR_SECOND_FRACTION = 2 / 3  # Second extreme at 2/3 of bar
    NOISE_SCALE = 0.1               # Noise relative to bar range
    FLAT_BAR_RANGE = 0.001          # Fallback range when high == low

    def __init__(self, seed: int | None = None, noise_factor: float = 0.3):
        self._rng = _random.Random(seed)
        self._noise_factor = noise_factor

    def generate(self, bar: Bar, num_points: int = 60) -> list[PriceTick]:
        if num_points < self.MIN_POINTS:
            num_points = self.MIN_POINTS

        o = float(bar.open)
        h = float(bar.high)
        l = float(bar.low)
        c = float(bar.close)

        # Decide order of extremes
        bullish = c >= o
        low_first_prob = self.DIRECTIONAL_BIAS if bullish else (1 - self.DIRECTIONAL_BIAS)
        low_first = self._rng.random() < low_first_prob

        if low_first:
            first_extreme = l
            second_extreme = h
        else:
            first_extreme = h
            second_extreme = l

        # Anchor positions
        anchor1_idx = int(num_points * self.ANCHOR_FIRST_FRACTION)
        anchor2_idx = int(num_points * self.ANCHOR_SECOND_FRACTION)
        last_idx = num_points - 1

        anchors = {
            0: o,
            anchor1_idx: first_extreme,
            anchor2_idx: second_extreme,
            last_idx: c,
        }

        # Sort anchor indices
        sorted_anchors = sorted(anchors.items())

        # Interpolate between anchors
        prices = [0.0] * num_points
        for i in range(len(sorted_anchors) - 1):
            idx_start, price_start = sorted_anchors[i]
            idx_end, price_end = sorted_anchors[i + 1]
            span = idx_end - idx_start
            for j in range(span + 1):
                idx = idx_start + j
                if idx >= num_points:
                    break
                t = j / max(span, 1)
                base = price_start + (price_end - price_start) * t
                # Add noise except at anchor points
                if idx in anchors:
                    prices[idx] = anchors[idx]
                else:
                    bar_range = h - l if h != l else self.FLAT_BAR_RANGE
                    noise = self._rng.gauss(0, bar_range * self._noise_factor * self.NOISE_SCALE)
                    noisy = base + noise
                    # Clamp to bar range
                    prices[idx] = max(l, min(h, noisy))

        # Guarantee exact high and low appear
        prices[anchor1_idx] = first_extreme
        prices[anchor2_idx] = second_extreme

        return [
            PriceTick(minute_offset=i, price=Decimal(str(round(p, 10))))
            for i, p in enumerate(prices)
        ]
