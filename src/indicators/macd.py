"""MACD — Moving Average Convergence/Divergence."""

from __future__ import annotations

import pandas as pd


def calc_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, list[float]]:
    """Return MACD components as a dict with keys ``macd``, ``signal``, ``hist``.

    ``macd``   — EMA(fast) − EMA(slow)
    ``signal`` — EMA(macd, signal)
    ``hist``   — macd − signal  (positive = bullish momentum)

    Early values are NaN until the slow EMA and signal line have filled.
    """
    s         = pd.Series(closes, dtype=float)
    macd_line = s.ewm(span=fast,   min_periods=fast).mean() \
              - s.ewm(span=slow,   min_periods=slow).mean()
    sig_line  = macd_line.ewm(span=signal, min_periods=signal).mean()
    return {
        "macd":   macd_line.tolist(),
        "signal": sig_line.tolist(),
        "hist":   (macd_line - sig_line).tolist(),
    }
