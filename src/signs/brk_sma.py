"""brk_sma — SMA Breakout sign detector. See docs/signs/brk_sma.md.

**2026-05-18 ship: low-edge whole-bar gate with K=3 prior lookback.**

Defaults changed from (close, K=5) to (low, K=3) after the
3-arm confluence A/B (`src/analysis/confluence_brk_sma_variant_ab.py`,
2026-05-18) showed +0.62 Sharpe at N=3 (+2.64 → +3.26) and +1.35 Sharpe
at N=2.  C control (close, K=3) showed only +0.12 Sharpe gain,
confirming the lift comes from the close→low whole-bar swap, not from
the K=5 → K=3 lookback change.

Per-FY consistency unchanged (5/7 non-negative for both arms at N=3).
FY2024 — the per-fire warning year — actually flipped from −0.90 to
+4.16 at strategy level; confluence gate filtered out the weak new
fires that hurt the per-fire DR.

The detector's `gate_use_low: bool` and `min_below_bars: int`
parameters remain configurable for future experiments.
"""

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
        "**SMA Breakout (strict whole-bar)** — "
        "today's LOW crosses above the N-bar simple moving average AND "
        "the prior 3 bars all had low ≤ SMA AND today's volume ≥ 1.5× "
        "the rolling-mean volume.  Resumption of the uptrend with no "
        "intraday retracement through the SMA."
    ),
}


class BrkSmaDetector:
    """Initialise once per stock hourly cache; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        window: int = 20,
        min_below_bars: int = 3,
        volume_mult: float = 1.5,
        gate_use_low: bool = True,
    ) -> None:
        """Detect SMA breakouts.

        Args:
            min_below_bars: number of prior bars that must have edge ≤ SMA
                (default 3, was 5 pre-2026-05-18).
            gate_use_low: if True (default, post-2026-05-18 ship), the cross
                check uses bar low (strict whole-bar — same convention as
                brk_wall/brk_kumo/brk_tenkan).  If False, uses bar close
                (pre-2026-05-18 behavior, still available for experiments).
        """
        self._stock_code      = stock_cache.stock_code
        self._dts             = [b.dt for b in stock_cache.bars]
        self._min_below_bars  = min_below_bars
        self._volume_mult     = volume_mult
        self._gate_use_low    = gate_use_low

        close_s    = pd.Series({b.dt: b.close for b in stock_cache.bars})
        low_s      = pd.Series({b.dt: b.low   for b in stock_cache.bars})
        min_p      = max(5, window // 2)
        self._sma   = close_s.rolling(window, min_periods=min_p).mean()
        self._close = close_s
        self._low   = low_s

        vol_s         = pd.Series({b.dt: b.volume for b in stock_cache.bars}, dtype=float)
        self._volume  = vol_s
        self._vol_avg = vol_s.rolling(window, min_periods=min_p).mean()

        self._fire_events: list[tuple[int, float]] = self._scan()

    def _scan(self) -> list[tuple[int, float]]:
        events: list[tuple[int, float]] = []
        dts = self._dts
        N = self._min_below_bars
        edge_series = self._low if self._gate_use_low else self._close
        for i in range(N, len(dts)):
            dt = dts[i]
            # Current bar's edge (low if gate_use_low else close) must exceed SMA.
            e = edge_series.get(dt)
            s = self._sma.get(dt)
            if e is None or s is None:
                continue
            e, s = float(e), float(s)
            if pd.isna(e) or pd.isna(s) or s == 0 or e <= s:
                continue
            # Score uses close for stability (current contract).
            c = self._close.get(dt)
            if c is None:
                continue
            c = float(c)
            # Volume confirmation: today's volume ≥ mult × rolling-mean volume.
            v   = self._volume.get(dt)
            v_a = self._vol_avg.get(dt)
            if v is None or v_a is None:
                continue
            v, v_a = float(v), float(v_a)
            if pd.isna(v) or pd.isna(v_a) or v_a <= 0 or v < self._volume_mult * v_a:
                continue
            # Prior N bars must all have edge at-or-below SMA.
            consolidated = True
            for k in range(1, N + 1):
                pj = edge_series.get(dts[i - k])
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
            # Situational: price still above SMA (same edge series as gate)
            dt_now = self._dts[idx]
            edge_now_series = self._low if self._gate_use_low else self._close
            e_now  = edge_now_series.get(dt_now)
            s_now  = self._sma.get(dt_now)
            if e_now is None or s_now is None:
                return None
            if pd.isna(float(e_now)) or pd.isna(float(s_now)) or float(e_now) <= float(s_now):
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
