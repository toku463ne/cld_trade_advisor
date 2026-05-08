"""Ichimoku Kinkou Hyou (一目均衡表).

Five lines:
  tenkan   (転換線) — Conversion Line  : (max_high + min_low) / 2  over tenkan_period bars
  kijun    (基準線) — Base Line        : same formula over kijun_period bars
  senkou_a (先行スパンA) — Leading Span A : (tenkan + kijun) / 2, plotted +displacement bars ahead
  senkou_b (先行スパンB) — Leading Span B : (max_high + min_low) / 2 over senkou_b_period bars,
                                           plotted +displacement bars ahead
  chikou   (遅行スパン) — Lagging Span  : current close, plotted -displacement bars behind

The area between senkou_a and senkou_b forms the "cloud" (雲, Kumo).
  Bullish cloud: senkou_a > senkou_b  (green)
  Bearish cloud: senkou_b > senkou_a  (red)

Standard Japanese stock parameters: 9 / 26 / 52 / 26.
Weekly charts sometimes use 7 / 26 / 52 / 26.

Return format
-------------
All series are length n (same as input).  Displacement shifts must be applied
by the caller when plotting:
  senkou_a / senkou_b → display at index i + displacement
  chikou              → display at index i - displacement

For signal computation use the already-shifted helpers:
  cloud_a[i] = senkou_a[i - displacement]   (cloud A visible at bar i)
  cloud_b[i] = senkou_b[i - displacement]   (cloud B visible at bar i)

Example::

    result = calc_ichimoku(highs, lows, closes)
    # check if price is above the cloud
    i = -1
    d = result["displacement"]
    above = closes[i] > max(result["senkou_a"][i - d], result["senkou_b"][i - d])
"""

from __future__ import annotations

import math

import pandas as pd


def _midpoint(highs: pd.Series, lows: pd.Series, period: int) -> pd.Series:
    return (highs.rolling(period).max() + lows.rolling(period).min()) / 2.0


def calc_ichimoku(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    displacement: int = 26,
) -> dict[str, list[float] | int]:
    """Return all five Ichimoku components plus the displacement value.

    Parameters
    ----------
    highs, lows, closes:
        Per-bar OHLC arrays (same length).
    tenkan_period:
        Conversion-line lookback (default 9).
    kijun_period:
        Base-line lookback (default 26).
    senkou_b_period:
        Leading-span-B lookback (default 52).
    displacement:
        Number of bars senkou lines are shifted forward / chikou backward
        (default 26).

    Returns
    -------
    dict with keys:
        ``tenkan``      list[float], length n
        ``kijun``       list[float], length n
        ``senkou_a``    list[float], length n  — plot at i + displacement
        ``senkou_b``    list[float], length n  — plot at i + displacement
        ``chikou``      list[float], length n  — plot at i - displacement
        ``displacement`` int
    """
    hi = pd.Series(highs,  dtype=float)
    lo = pd.Series(lows,   dtype=float)
    cl = pd.Series(closes, dtype=float)

    tenkan  = _midpoint(hi, lo, tenkan_period)
    kijun   = _midpoint(hi, lo, kijun_period)
    senkou_a = (tenkan + kijun) / 2.0
    senkou_b = _midpoint(hi, lo, senkou_b_period)

    def _tolist(s: pd.Series) -> list[float]:
        return [v if not math.isnan(v) else float("nan") for v in s.tolist()]

    return {
        "tenkan":       _tolist(tenkan),
        "kijun":        _tolist(kijun),
        "senkou_a":     _tolist(senkou_a),
        "senkou_b":     _tolist(senkou_b),
        "chikou":       _tolist(cl),
        "displacement": displacement,
    }
