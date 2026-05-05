"""Simple moving correlation indicator.

Computes the rolling N-day Pearson correlation of *returns* between a stock
and each of a set of reference indicators (e.g. major market indices).

Using return correlation avoids the spurious level-correlation problem that
plagues raw price series (two trending-up assets look correlated even with
unrelated day-to-day moves).
"""

from __future__ import annotations

import pandas as pd


def compute_moving_corr(
    stock_series: pd.Series,
    indicator_map: dict[str, pd.Series],
    window: int = 20,
) -> dict[str, pd.Series]:
    """Return rolling Pearson correlation of returns for each indicator.

    Parameters
    ----------
    stock_series:
        Close prices for the stock, DatetimeIndex (timezone-naive date-level).
    indicator_map:
        Mapping of indicator_code → close prices (same index convention).
    window:
        Rolling window in trading days.

    Returns
    -------
    dict mapping indicator_code → pd.Series of correlation values in [-1, 1].
    The index matches the union of stock and indicator dates.
    NaN for the first ``window - 1`` bars and wherever data is missing.
    """
    min_periods = max(5, window // 2)
    stock_ret = stock_series.pct_change()
    result: dict[str, pd.Series] = {}

    for code, ind_series in indicator_map.items():
        ind_ret = ind_series.pct_change()
        aligned = pd.concat(
            [stock_ret.rename("stock"), ind_ret.rename("ind")],
            axis=1,
        )
        corr = (
            aligned["stock"]
            .rolling(window, min_periods=min_periods)
            .corr(aligned["ind"])
        )
        result[code] = corr

    return result
