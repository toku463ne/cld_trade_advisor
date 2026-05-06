"""brk_bol — Bollinger Band Breakout sign detector.

Fires on the bar where close crosses from below to above the upper Bollinger Band
(N-bar SMA + n_std × rolling σ).

Score = min(0.5 + excess × 0.5, 1.0)
  excess = (close − upper_band) / σ   [how many σ above the upper band]
  A close right at the upper band scores 0.5; each additional σ adds 0.5 more.

Valid for up to ``valid_bars`` bars after firing, provided close remains > upper band.
If price retreats below the upper band the sign expires early.
"""

from __future__ import annotations

import bisect
import datetime

import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache


class BrkBolDetector:
    """Initialise once per stock hourly cache; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        window: int  = 20,
        n_std: float = 2.0,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        close_s   = pd.Series({b.dt: b.close for b in stock_cache.bars})
        min_p     = max(5, window // 2)
        mid       = close_s.rolling(window, min_periods=min_p).mean()
        std       = close_s.rolling(window, min_periods=min_p).std()
        self._upper  = mid + n_std * std
        self._std    = std
        self._close  = close_s

        self._fire_events: list[tuple[int, float]] = self._scan()

    def _scan(self) -> list[tuple[int, float]]:
        events: list[tuple[int, float]] = []
        dts = self._dts
        for i in range(1, len(dts)):
            dt, prev_dt = dts[i], dts[i - 1]
            c   = self._close.get(dt)
            pc  = self._close.get(prev_dt)
            u   = self._upper.get(dt)
            pu  = self._upper.get(prev_dt)
            sig = self._std.get(dt)
            if any(x is None for x in [c, pc, u, pu, sig]):
                continue
            c, pc, u, pu, sig = float(c), float(pc), float(u), float(pu), float(sig)
            if any(pd.isna(x) for x in [c, pc, u, pu, sig]) or sig == 0:
                continue
            if pc <= pu and c > u:
                excess = (c - u) / sig
                score  = min(0.5 + excess * 0.5, 1.0)
                events.append((i, score))
        return events

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 3,
    ) -> SignResult | None:
        idx = bisect.bisect_right(self._dts, as_of) - 1
        if idx < 0 or not self._fire_events:
            return None

        for fi, score in reversed(self._fire_events):
            if fi > idx:
                continue
            if idx - fi > valid_bars:
                break
            # Situational: price still above upper band
            dt_now = self._dts[idx]
            c_now  = self._close.get(dt_now)
            u_now  = self._upper.get(dt_now)
            if c_now is None or u_now is None:
                return None
            if pd.isna(float(c_now)) or pd.isna(float(u_now)) or float(c_now) <= float(u_now):
                return None

            valid_until_idx = min(fi + valid_bars, len(self._dts) - 1)
            return SignResult(
                sign_type="brk_bol",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
