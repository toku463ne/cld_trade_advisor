"""Wilder RSI (Relative Strength Index)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def calc_rsi(closes: list[float], period: int = 14) -> list[float]:
    """Return Wilder RSI series.

    Uses EWM with ``com=period-1`` (equivalent to Wilder smoothing).
    First values are NaN until *period* bars have accumulated.
    Output range is [0, 100]; NaN where insufficient data.
    """
    s     = pd.Series(closes, dtype=float)
    delta = s.diff()
    avg_g = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    avg_l = (-delta).clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return (100.0 - 100.0 / (1.0 + rs)).tolist()
