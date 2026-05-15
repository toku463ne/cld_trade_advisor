"""brk_sma — SMA Breakout sign detector. See docs/signs/brk_sma.md."""

from __future__ import annotations

import bisect
import datetime

import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_SCORE_SMA_CAP = 0.02  # distance at which score saturates to 1.0

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["brk_sma"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "brk_sma": (
        "**SMA Breakout** — "
        "price crosses from below to above the N-bar simple moving average. "
        "Validates resumption of the uptrend."
    ),
}


class BrkSmaDetector:
    """Initialise once per stock hourly cache; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        window: int = 20,
        min_below_bars: int = 5,
        volume_mult: float = 1.5,
    ) -> None:
        self._stock_code      = stock_cache.stock_code
        self._dts             = [b.dt for b in stock_cache.bars]
        self._min_below_bars  = min_below_bars
        self._volume_mult     = volume_mult

        close_s    = pd.Series({b.dt: b.close for b in stock_cache.bars})
        min_p      = max(5, window // 2)
        self._sma  = close_s.rolling(window, min_periods=min_p).mean()
        self._close = close_s

        vol_s         = pd.Series({b.dt: b.volume for b in stock_cache.bars}, dtype=float)
        self._volume  = vol_s
        self._vol_avg = vol_s.rolling(window, min_periods=min_p).mean()

        self._fire_events: list[tuple[int, float]] = self._scan()

    def _scan(self) -> list[tuple[int, float]]:
        events: list[tuple[int, float]] = []
        dts = self._dts
        N = self._min_below_bars
        for i in range(N, len(dts)):
            # Current bar must close above SMA.
            dt = dts[i]
            c  = self._close.get(dt)
            s  = self._sma.get(dt)
            if c is None or s is None:
                continue
            c, s = float(c), float(s)
            if pd.isna(c) or pd.isna(s) or s == 0 or c <= s:
                continue
            # Volume confirmation: today's volume ≥ mult × rolling-mean volume.
            v   = self._volume.get(dt)
            v_a = self._vol_avg.get(dt)
            if v is None or v_a is None:
                continue
            v, v_a = float(v), float(v_a)
            if pd.isna(v) or pd.isna(v_a) or v_a <= 0 or v < self._volume_mult * v_a:
                continue
            # Prior N bars must all close at-or-below SMA.
            consolidated = True
            for k in range(1, N + 1):
                pj = self._close.get(dts[i - k])
                sj = self._sma.get(dts[i - k])
                if pj is None or sj is None:
                    consolidated = False
                    break
                pj, sj = float(pj), float(sj)
                if pd.isna(pj) or pd.isna(sj) or pj > sj:
                    consolidated = False
                    break
            if not consolidated:
                continue
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
