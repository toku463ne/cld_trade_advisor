"""brk_wall — break above a recent sideways-range wall. See docs/signs/brk_wall.md.

Operator hypothesis (2026-05-17 /sign-debate): sideways price ranges
in the recent past form "walls" — tested resistance levels.  A clean
breakout above such a wall (`low[T] > wall AND low[T-1] ≤ wall`) is a
meaningful bullish event, distinct from generic rolling-N-max breakouts.

Probe basis: `src/analysis/brk_wall_probe.py` (committed 8a10ee4)
showed standalone DR 72.6% / EV +2.88% on 4,733 training fires.  The
probe over-estimated DR by ~20pp because it used globally-detected
zigzag peaks (more confirmed) vs the canonical pipeline's per-fire
windowed peaks; canonical rebench numbers are weaker but still pass:

  - Pooled FY2018–FY2024: n=5,006, DR=53.0%, p<0.001, perm_pass 2/7
    (FY2023 + FY2025 OOS); FY2019 and FY2022 ran sub-50% DR
  - FY2025 OOS: n=1,005, DR=59.6%, p<0.001, perm_p<0.001
    - bear DR=65.7% (p<0.001), bull DR=55.6% (p≈0.006)
  - Kumo inside cell carries highest EV (~inside +0.049)
  - Score calibration: ρ=0.028, noise — drop score from ranking key
    (treat brk_wall like rev_nhi: rank by EV only)
  - Confluence-incremental NEGATIVE: do NOT add to bullish-set tally;
    ship as standalone proposal only

Operator-chosen params (K=10, θ=0.05, lookback=120) reproduced here.
**Now constructor-parameterized** so K/θ/lookback can be overridden for
experiments; defaults preserve current production.

**K-sweep 2026-05-18** (`src/analysis/brk_wall_k_sweep.py` + the two
A/B scripts):

  Per-fire pooled across FY2019–FY2025:
    K=10 (current): n=5824, DR=52.9%, mean_r=+0.80%
    K=15          : n=3449, DR=53.7%, mean_r=+0.86%  (+0.8pp DR)
    K=20          : n=1613, DR=53.5%, mean_r=+0.83%
    K=30          : n=314,  DR=51.0%, mean_r=+0.83%  (too sparse)

  Score informativeness — uninformative at ALL K values:
    K=10 Spearman ρ=+0.020 (p=0.122), Q4−Q1 spread +0.81pp — WEAK
    K=15 Spearman ρ=+0.014 (p=0.400), Q4−Q1 spread +0.82pp — WEAK
    K=20 Spearman ρ=-0.003 (p=0.909), Q4−Q1 spread -0.22pp — NOISE
    K=30 Spearman ρ=-0.001 (p=0.992), Q4−Q1 spread +0.41pp — NOISE

  Strategy A/B verdicts (K=15 tested, the per-fire winner):
    - regime_sign A/B (K=15): trade-for-trade IDENTICAL with vs without
      brk_wall — same result as K=10.  brk_wall remains inert in
      regime_sign at any K.
    - Confluence inclusion A/B (K=15): adding brk_wall to bullish set
      REGRESSES Sharpe at N=3 (+3.72 → +2.32, Δ −1.40), 5/7 FYs lose.
      Same dilution finding as K=10.

  Final decision: **revert to K=10 default.**  Per-fire +0.8pp gain at
  K=15 doesn't translate to any strategy lift.  K parameter remains
  configurable for future experiments.

  See `docs/analysis/brk_wall_tuning.md` for full per-FY tables.
"""
from __future__ import annotations

import bisect
import datetime

import numpy as np
import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_K        = 10     # sideways window length (trading days) — default (K-sweep 2026-05-18 confirmed K=10; K=15 had +0.8pp per-fire DR but zero/negative strategy impact)
_THETA    = 0.05   # (max H − min L) / mean C tightness threshold — default
_LOOKBACK = 120    # bars over which to search for walls (~6 months) — default

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["brk_wall"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "brk_wall": (
        "**Breakout above sideways-range wall** — "
        "today's low closes above the highest recently-tested resistance level "
        "(highs of any 10-bar tight consolidation in the prior 6 months), "
        "and yesterday's low did not.  Strict clean breakout, no intraday "
        "retracement."
    ),
}


class BrkWallDetector:
    """Initialise once per stock cache; call detect() per bar.

    Derives daily H/L/C internally from the (possibly hourly) cache, so the
    detector behaves identically whether dispatched against an hourly or
    daily cache.  Fire bar_index is the first bar of the fire day, mirroring
    the str_hold pattern.
    """

    def __init__(self, stock_cache: DataCache,
                 K: int = _K, theta: float = _THETA,
                 lookback: int = _LOOKBACK) -> None:
        if K < 3:
            raise ValueError(f"K must be >=3, got {K}")
        if lookback <= K + 1:
            raise ValueError(f"lookback ({lookback}) must exceed K+1 ({K+1})")
        self._K_param        = K
        self._theta_param    = theta
        self._lookback_param = lookback
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        # Derive per-trade-date OHLC from the cache bars (UTC date = JST trade date).
        date_hi:  dict[datetime.date, float] = {}
        date_lo:  dict[datetime.date, float] = {}
        date_cl:  dict[datetime.date, float] = {}
        date_first_bar_idx: dict[datetime.date, int] = {}
        for i, b in enumerate(stock_cache.bars):
            d = b.dt.date()
            if d not in date_hi:
                date_hi[d] = b.high
                date_lo[d] = b.low
                date_first_bar_idx[d] = i
            else:
                if b.high > date_hi[d]:
                    date_hi[d] = b.high
                if b.low  < date_lo[d]:
                    date_lo[d] = b.low
            date_cl[d] = b.close   # last bar of the day → daily close
        self._date_first_bar_idx = date_first_bar_idx

        trading_dates = sorted(date_hi)
        if len(trading_dates) < self._lookback_param + 5:
            self._fire_events: list[tuple[int, float]] = []
            return

        highs = np.array([date_hi[d] for d in trading_dates], dtype=float)
        lows  = np.array([date_lo[d] for d in trading_dates], dtype=float)
        closes = np.array([date_cl[d] for d in trading_dates], dtype=float)

        K        = self._K_param
        theta    = self._theta_param
        lookback = self._lookback_param

        # 1. tight_window_high[i] = highs[i-K+1..i].max() if window tight, else NaN
        n = len(trading_dates)
        tight_high = np.full(n, np.nan)
        for i in range(K - 1, n):
            wnd_hi = highs[i  - K + 1 : i + 1].max()
            wnd_lo = lows[i   - K + 1 : i + 1].min()
            wnd_mn = closes[i - K + 1 : i + 1].mean()
            if wnd_mn > 0 and (wnd_hi - wnd_lo) / wnd_mn <= theta:
                tight_high[i] = wnd_hi

        # 2. wall[T] = max(tight_high[j] for j in [T-lookback, T-K-1])
        s = pd.Series(tight_high).shift(K + 1)
        wall = s.rolling(lookback - K, min_periods=1).max().to_numpy()

        # 3. Strict transition-gated fire: low[T] > wall[T-1] AND low[T-1] <= wall[T-1]
        fire_events: list[tuple[int, float]] = []
        for T in range(1, n):
            w = wall[T - 1]
            if np.isnan(w) or w <= 0:
                continue
            if not (lows[T] > w and lows[T - 1] <= w):
                continue
            fire_date = trading_dates[T]
            bar_idx = date_first_bar_idx.get(fire_date)
            if bar_idx is None:
                continue
            # Score = (close − wall) / wall, normalised by 5% to saturate
            score = float(min((closes[T] - w) / w, 0.05) / 0.05)
            fire_events.append((bar_idx, score))
        self._fire_events = fire_events

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 5,
    ) -> SignResult | None:
        """Return the most recent valid brk_wall fire at *as_of*, or None.

        `valid_bars` counts hourly bars in the cache for hourly granularity,
        or daily bars for daily granularity — same convention as brk_sma.
        """
        idx = bisect.bisect_right(self._dts, as_of) - 1
        if idx < 0 or not self._fire_events:
            return None

        for fi, score in reversed(self._fire_events):
            if fi > idx:
                continue
            if idx - fi > valid_bars:
                break
            valid_until_idx = min(fi + valid_bars, len(self._dts) - 1)
            return SignResult(
                sign_type="brk_wall",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
