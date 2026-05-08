"""Exponential Moving Average."""

from __future__ import annotations

import pandas as pd


def calc_ema(closes: list[float], period: int) -> list[float]:
    """Return EMA of *closes* with span=*period*.

    Uses ``min_periods=period`` so early values are NaN until the window fills.
    """
    return pd.Series(closes, dtype=float).ewm(span=period, min_periods=period).mean().tolist()
