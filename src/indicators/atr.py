"""Average True Range (ATR)."""

from __future__ import annotations

import pandas as pd


def calc_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[float]:
    """Return Wilder ATR series.

    True Range = max(H−L, |H−prev_C|, |L−prev_C|).
    Smoothed with EWM (com=period−1), matching Wilder's original formula.
    First values are NaN until *period* bars have accumulated.
    """
    hi, lo, cl = (
        pd.Series(highs,  dtype=float),
        pd.Series(lows,   dtype=float),
        pd.Series(closes, dtype=float),
    )
    prev = cl.shift(1)
    tr   = pd.concat([hi - lo, (hi - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean().tolist()
