"""Bollinger Bands (SMA ± nstd * rolling std)."""

from __future__ import annotations

import pandas as pd


def calc_bb(
    closes: list[float],
    period: int = 20,
    nstd: float = 2.0,
) -> tuple[list[float], list[float], list[float]]:
    """Return (lower, mid, upper) Bollinger Band series.

    *mid* is the SMA(*period*).  *lower* / *upper* are mid ± *nstd* × σ.
    First ``period - 1`` values are NaN in all three series.
    """
    s   = pd.Series(closes, dtype=float)
    mid = s.rolling(period).mean()
    std = s.rolling(period).std(ddof=1)
    return (mid - nstd * std).tolist(), mid.tolist(), (mid + nstd * std).tolist()
