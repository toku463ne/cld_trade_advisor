"""Simple Moving Average."""

from __future__ import annotations

import pandas as pd


def calc_sma(closes: list[float], period: int) -> list[float]:
    """Return SMA of *closes* with the given *period*.

    First ``period - 1`` values are NaN.
    """
    return pd.Series(closes, dtype=float).rolling(period).mean().tolist()
