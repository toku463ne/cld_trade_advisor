"""brk_sma — SMA Breakout sign detector.

Fires on the bar where close crosses from below to above the N-bar SMA.

Score = min((close − SMA) / SMA, 0.02) / 0.02
  Normalised distance above SMA at the crossing bar; saturates at 2 %.

Valid for up to ``valid_bars`` bars after firing, provided close remains > SMA.
The sign expires early if price falls back below the SMA.
"""

from __future__ import annotations

import bisect
import datetime

import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_SCORE_SMA_CAP = 0.02  # distance at which score saturates to 1.0


class BrkSmaDetector:
    """Initialise once per stock hourly cache; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        window: int = 20,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        close_s    = pd.Series({b.dt: b.close for b in stock_cache.bars})
        min_p      = max(5, window // 2)
        self._sma  = close_s.rolling(window, min_periods=min_p).mean()
        self._close = close_s

        self._fire_events: list[tuple[int, float]] = self._scan()

    def _scan(self) -> list[tuple[int, float]]:
        events: list[tuple[int, float]] = []
        dts = self._dts
        for i in range(1, len(dts)):
            dt, prev_dt = dts[i], dts[i - 1]
            c  = self._close.get(dt)
            pc = self._close.get(prev_dt)
            s  = self._sma.get(dt)
            ps = self._sma.get(prev_dt)
            if any(x is None for x in [c, pc, s, ps]):
                continue
            c, pc, s, ps = float(c), float(pc), float(s), float(ps)
            if any(pd.isna(x) for x in [c, pc, s, ps]) or s == 0:
                continue
            if pc <= ps and c > s:
                score = min((c - s) / s, _SCORE_SMA_CAP) / _SCORE_SMA_CAP
                events.append((i, score))
        return events

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
            # Situational: price still above SMA
            dt_now = self._dts[idx]
            c_now  = self._close.get(dt_now)
            s_now  = self._sma.get(dt_now)
            if c_now is None or s_now is None:
                return None
            if pd.isna(float(c_now)) or pd.isna(float(s_now)) or float(c_now) <= float(s_now):
                return None

            valid_until_idx = min(fi + valid_bars, len(self._dts) - 1)
            return SignResult(
                sign_type="brk_sma",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
