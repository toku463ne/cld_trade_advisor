"""_trend_score — runtime helper for the operator's 5-feature trend_score.

Stage 0 ([[project-trend-score-stage0]]) measured per-sign decile-EV on this
score; Stage 1 A/Bs (floor for confluence, ceiling for regime_sign) need
to look up trend_score at fire date and filter candidates accordingly.

This module factors the feature computation out of
`src.analysis.trend_score_stage0._compute_features` so callers can compute
a per-stock {date: trend_score} map from a DataCache they already have.

Public surface:
    compute_trend_score(cache) -> dict[date, float]   # in [0, 100]

Constants are intentionally re-declared here (mirroring Stage 0) so the
runtime path doesn't depend on the probe script staying intact.
"""
from __future__ import annotations

import datetime
import math

import numpy as np
import pandas as pd

from src.indicators.ichimoku import calc_ichimoku
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache

# Pre-registered Stage 0 constants — DO NOT change without re-running Stage 0.
_SMA_N       = 50
_PEAK_SIZE   = 5
_PEAK_MID    = 2
_LONG_MAX_N  = 252
_CHIKO_LAG   = 26
_KUMO_DISP   = 26
_ROLLING_WIN = 250


def compute_trend_score(cache: DataCache) -> dict[datetime.date, float]:
    """Return {date: trend_score} for every bar with enough history.

    Returns {} if the cache lacks enough bars (~500) for the rolling
    percentile rank to be meaningful.

    Score construction (per-stock, look-ahead-safe):
        f_sma       = (close - sma_50) / sma_50
        f_peak      = signed_magnitude of last confirmed zigzag leg
                      (confirmed at bar_index + size, so causal)
        f_kumo      = (close - visible_kumo_midline) / midline
        f_chiko     = (close - close[-26]) / close[-26]
        f_long_max  = (close - max(close, 252)) / max
    Each feature → rolling-250-bar percentile rank in [0, 100].
    trend_score = mean of the 5 ranks ∈ [0, 100].
    """
    if len(cache.bars) < _LONG_MAX_N + _ROLLING_WIN:
        return {}

    dates  = [b.dt.date() for b in cache.bars]
    closes = np.array([b.close for b in cache.bars], dtype=float)
    highs  = np.array([b.high  for b in cache.bars], dtype=float)
    lows   = np.array([b.low   for b in cache.bars], dtype=float)
    n = len(closes)

    s_close = pd.Series(closes)
    sma50 = s_close.rolling(_SMA_N).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        f_sma = (closes - sma50) / sma50
    f_sma[~np.isfinite(f_sma)] = np.nan

    peaks = detect_peaks(highs.tolist(), lows.tolist(),
                         size=_PEAK_SIZE, middle_size=_PEAK_MID)
    confirmed = sorted(
        [(p.bar_index + _PEAK_SIZE, p.bar_index, p.price)
         for p in peaks if abs(p.direction) == 2],
        key=lambda t: t[0],
    )
    f_peak = np.full(n, np.nan, dtype=float)
    # Forward-walk: maintain a pointer into `confirmed` instead of re-scanning.
    j = 0
    confirmed_so_far: list[tuple[int, int, float]] = []
    for i in range(n):
        while j < len(confirmed) and confirmed[j][0] <= i:
            confirmed_so_far.append(confirmed[j])
            j += 1
        if len(confirmed_so_far) >= 2:
            _, _, prev_p = confirmed_so_far[-2]
            _, _, last_p = confirmed_so_far[-1]
            if prev_p > 0:
                f_peak[i] = (last_p - prev_p) / prev_p

    ichi = calc_ichimoku(highs.tolist(), lows.tolist(), closes.tolist())
    senkou_a = np.array(ichi["senkou_a"], dtype=float)
    senkou_b = np.array(ichi["senkou_b"], dtype=float)
    kumo_mid = np.full(n, np.nan, dtype=float)
    for i in range(_KUMO_DISP, n):
        a = senkou_a[i - _KUMO_DISP]
        b = senkou_b[i - _KUMO_DISP]
        if not (math.isnan(a) or math.isnan(b)):
            kumo_mid[i] = (a + b) / 2
    with np.errstate(divide="ignore", invalid="ignore"):
        f_kumo = (closes - kumo_mid) / kumo_mid
    f_kumo[~np.isfinite(f_kumo)] = np.nan

    f_chiko = np.full(n, np.nan, dtype=float)
    for i in range(_CHIKO_LAG, n):
        ref = closes[i - _CHIKO_LAG]
        if ref > 0:
            f_chiko[i] = (closes[i] - ref) / ref

    max_252 = pd.Series(closes).rolling(_LONG_MAX_N).max().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        f_long_max = (closes - max_252) / max_252
    f_long_max[~np.isfinite(f_long_max)] = np.nan

    df = pd.DataFrame({
        "f_sma":      f_sma,
        "f_peak":     f_peak,
        "f_kumo":     f_kumo,
        "f_chiko":    f_chiko,
        "f_long_max": f_long_max,
    })
    pct_cols = []
    for col in ("f_sma", "f_peak", "f_kumo", "f_chiko", "f_long_max"):
        pc = f"{col}_pct"
        df[pc] = (
            df[col].rolling(_ROLLING_WIN, min_periods=int(_ROLLING_WIN * 0.5))
                   .rank(pct=True) * 100.0
        )
        pct_cols.append(pc)
    ts = df[pct_cols].mean(axis=1, skipna=False).to_numpy()

    out: dict[datetime.date, float] = {}
    for i, d in enumerate(dates):
        v = ts[i]
        if not math.isnan(v):
            out[d] = float(v)
    return out


def build_score_map(stock_caches: dict[str, DataCache]
                   ) -> dict[str, dict[datetime.date, float]]:
    """Apply `compute_trend_score` over a dict of stock → cache."""
    out: dict[str, dict[datetime.date, float]] = {}
    for code, cache in stock_caches.items():
        out[code] = compute_trend_score(cache)
    return out
