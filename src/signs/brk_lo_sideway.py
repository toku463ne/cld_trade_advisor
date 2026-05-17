"""brk_lo_sideway — break below a recent sideways-range floor. See docs/signs/brk_lo_sideway.md.

Mirror of brk_hi_sideway: fires when today's bar **high** breaks below
the lowest recently-tested support floor — the low of any tight 10-bar
consolidation in the prior ~6 months.  Strict and transition-gated.

Probe vs canonical INVERSION (2026-05-17):
- Probe (`brk_hi_sideway_probe.py --side lo`, global zigzag): 6/7
  training FYs showed breakdown-persistence (long-entry EV −2.16%
  pooled).  Suggested "avoid long" framing.
- Canonical rebench (windowed zigzag, per-fire detection): pooled
  FY2018–FY2024 DR=51.7%, perm_pass 2/7 (FY2022 + FY2024); both pass
  FYs show MEAN-REVERSION (long wins after breakdown), not
  persistence.  FY2019 alone has DR=36.4% (operator's hypothesis
  right for that year) but perm_p=1.000 (no significance vs shuffle).

Net: as a LONG entry signal, brk_lo_sideway is mildly +EV (similar
tier to brk_sma/brk_bol) with strongest cell at Kumo-below
(DR 59.1% n=1173).  Score calibration ρ=+0.172 in high-corr cohort
(strongest in catalogue) — deeper breakdowns mean-revert MORE.

Do NOT use as "avoid long" filter — that interpretation contradicts
the canonical measurement.  Treat as a regular long-entry sign with
known regime concentration (Kumo below + bear-regime cells).
"""
from __future__ import annotations

import bisect
import datetime

import numpy as np
import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_K        = 10     # sideways window length (trading days)
_THETA    = 0.05   # (max H − min L) / mean C tightness threshold
_LOOKBACK = 120    # bars over which to search for floors (~6 months)

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["brk_lo_sideway"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "brk_lo_sideway": (
        "**Mean-reversion buy after sideways-range breakdown** — "
        "today's high closes below the lowest recently-tested support level "
        "(lows of any 10-bar tight consolidation in the prior 6 months), "
        "and yesterday's high did not.  Canonical measurement: mild long "
        "entry signal; strongest in Kumo-below cell (DR 59% n=1173).  "
        "Deeper breakdown → higher mean-reversion EV in high-corr cohort "
        "(ρ=+0.17, strongest score calibration in catalogue)."
    ),
}


class BrkLoSidewayDetector:
    """Initialise once per stock cache; call detect() per bar.

    Mirror of BrkHiSidewayDetector: looks for breakdowns below the lowest
    recently-tested support floor.  Daily H/L/C derived internally from
    the (possibly hourly) cache.
    """

    def __init__(self, stock_cache: DataCache) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

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
            date_cl[d] = b.close
        self._date_first_bar_idx = date_first_bar_idx

        trading_dates = sorted(date_hi)
        if len(trading_dates) < _LOOKBACK + 5:
            self._fire_events: list[tuple[int, float]] = []
            return

        highs = np.array([date_hi[d] for d in trading_dates], dtype=float)
        lows  = np.array([date_lo[d] for d in trading_dates], dtype=float)
        closes = np.array([date_cl[d] for d in trading_dates], dtype=float)

        # 1. tight_window_low[i] = lows[i-K+1..i].min() if window tight, else NaN
        n = len(trading_dates)
        tight_low = np.full(n, np.nan)
        for i in range(_K - 1, n):
            wnd_hi = highs[i  - _K + 1 : i + 1].max()
            wnd_lo = lows[i   - _K + 1 : i + 1].min()
            wnd_mn = closes[i - _K + 1 : i + 1].mean()
            if wnd_mn > 0 and (wnd_hi - wnd_lo) / wnd_mn <= _THETA:
                tight_low[i] = wnd_lo

        # 2. floor[T] = min(tight_low[j] for j in [T-lookback, T-K-1])
        s = pd.Series(tight_low).shift(_K + 1)
        floor = s.rolling(_LOOKBACK - _K, min_periods=1).min().to_numpy()

        # 3. Strict transition-gated fire: high[T] < floor[T-1] AND high[T-1] >= floor[T-1]
        fire_events: list[tuple[int, float]] = []
        for T in range(1, n):
            f = floor[T - 1]
            if np.isnan(f) or f <= 0:
                continue
            if not (highs[T] < f and highs[T - 1] >= f):
                continue
            fire_date = trading_dates[T]
            bar_idx = date_first_bar_idx.get(fire_date)
            if bar_idx is None:
                continue
            # Score = (floor − close) / floor, normalised by 5% (deeper breakdown → larger)
            score = float(min((f - closes[T]) / f, 0.05) / 0.05)
            fire_events.append((bar_idx, score))
        self._fire_events = fire_events

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 5,
    ) -> SignResult | None:
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
                sign_type="brk_lo_sideway",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
