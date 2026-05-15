"""div_vol — Volume-Confirmed N225 Divergence sign detector. See docs/signs/div_vol.md."""

from __future__ import annotations

import bisect
import datetime

import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_N225_RET_MAX  = -0.015
_STOCK_RET_MIN =  0.003
_CORR_MIN      =  0.30
_VOL_RATIO_MIN =  2.0

SIGN_VALID: bool = False  # fires intrabar; not suitable for daily regime_sign benchmark
SIGN_NAMES: list[str] = ["div_vol"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "div_vol": (
        "**Volume-Confirmed Bar Divergence** — "
        "div_bar conditions plus volume exceeds 2× the 20-bar average. "
        "High-volume divergence amplifies confidence in independent buying."
    ),
}


class DivVolDetector:
    """Initialise once per (stock, N225) hourly cache pair; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        n225_cache: DataCache,
        window: int = 20,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        stock_s = pd.Series({b.dt: b.close  for b in stock_cache.bars})
        n225_s  = pd.Series({b.dt: b.close  for b in n225_cache.bars})
        vol_s   = pd.Series({b.dt: float(b.volume) for b in stock_cache.bars})

        stock_ret = stock_s.pct_change()
        n225_ret  = n225_s.pct_change()
        min_p     = max(5, window // 2)

        aligned = pd.concat(
            [stock_ret.rename("stock"), n225_ret.rename("n225")],
            axis=1,
        )
        self._corr      = aligned["stock"].rolling(window, min_periods=min_p).corr(aligned["n225"])
        self._corr_prev = self._corr.shift(1)
        self._stock_ret = stock_ret
        self._n225_ret  = n225_ret
        self._vol       = vol_s
        self._vol_avg   = vol_s.rolling(window, min_periods=min_p).mean()

        self._fire_events: list[tuple[int, float]] = self._scan()

    def _scan(self) -> list[tuple[int, float]]:
        events: list[tuple[int, float]] = []
        for i, dt in enumerate(self._dts):
            sr = self._stock_ret.get(dt)
            nr = self._n225_ret.get(dt)
            cr = self._corr_prev.get(dt)
            v  = self._vol.get(dt)
            va = self._vol_avg.get(dt)
            if any(x is None for x in [sr, nr, cr, v, va]):
                continue
            sr, nr, cr, v, va = float(sr), float(nr), float(cr), float(v), float(va)
            if any(pd.isna(x) for x in [sr, nr, cr, v, va]) or va == 0:
                continue
            vol_ratio = v / va
            if (nr < _N225_RET_MAX and sr > _STOCK_RET_MIN
                    and cr > _CORR_MIN and vol_ratio >= _VOL_RATIO_MIN):
                base  = (sr - nr) * cr
                bonus = min(vol_ratio / _VOL_RATIO_MIN, 3.0) / 3.0
                events.append((i, base * (1.0 + bonus)))
        return events

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 5,
    ) -> SignResult | None:
        idx = bisect.bisect_right(self._dts, as_of) - 1
        if idx < 0 or not self._fire_events:
            return None

        fire_idx: int | None = None
        fire_score: float    = 0.0
        for fi, score in reversed(self._fire_events):
            if fi > idx:
                continue
            if idx - fi > valid_bars:
                break
            fire_idx, fire_score = fi, score
            break

        if fire_idx is None:
            return None

        corr_now = self._corr.get(self._dts[idx])
        if corr_now is None or pd.isna(float(corr_now)) or float(corr_now) >= 0:
            return None

        valid_until_idx = min(fire_idx + valid_bars, len(self._dts) - 1)
        return SignResult(
            sign_type="div_vol",
            stock_code=self._stock_code,
            score=fire_score,
            fired_at=self._dts[fire_idx],
            valid_until=self._dts[valid_until_idx],
        )
