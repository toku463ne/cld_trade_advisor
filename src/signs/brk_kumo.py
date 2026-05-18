"""brk_kumo — break above / below the Ichimoku kumo (cloud).

One detector, both directions via the ``side`` argument:

  side="hi" → SIGN_TYPE "brk_kumo_hi" — low[T] crosses ABOVE kumo top
  side="lo" → SIGN_TYPE "brk_kumo_lo" — high[T] crosses BELOW kumo bottom

Operator preference (2026-05-18): use **low** for hi-side and **high**
for lo-side, NOT close.  This matches brk_wall / brk_floor convention
and enforces a true "stage change" — the ENTIRE bar is on the new
side of the level, no intraday retracement.

Kumo at bar T is the cloud as conventionally drawn at bar T, which is
computed from data ending bar T - displacement (default 26).  So:

  kumo_top[T] = max(senkou_a[T-26], senkou_b[T-26])
  kumo_bot[T] = min(senkou_a[T-26], senkou_b[T-26])

Transition-gated fire (the prior bar must NOT have been on the new
side already — fires once per crossing).

Built 2026-05-18 per operator request for situational picture signs.

**Canonical rebench (2026-05-18, strict whole-bar version):**
- brk_kumo_hi: pooled DR ~51% (FY2018–FY2024), FY2025 OOS 54.2% (n=732,
  p=0.022), perm_pass 3/7
- brk_kumo_lo: pooled DR ~52%, FY2025 OOS 52.2% (n=492, weakest of 6
  new signs), perm_pass 2/7

**Confluence A/B (`src/analysis/confluence_ichimoku_ab.py`, 2026-05-18):**
After switching to strict whole-bar (low > level) interpretation, the
expanded confluence set (baseline + brk_kumo_hi + brk_tenkan_hi +
chiko_hi) was tied with baseline at N≥3 (+3.64 vs +3.80, Δ −0.16 within
noise) with better mean_r and win%.  Per operator decision (2026-05-18),
**brk_kumo_hi ADDED to ConfluenceSignStrategy bullish set** (10 signs
total).  brk_kumo_lo remains catalogue-only.
"""
from __future__ import annotations

import bisect
import datetime

import numpy as np

from src.indicators.ichimoku import calc_ichimoku
from src.signs.base import SignResult
from src.simulator.cache import DataCache

_TENKAN_P   = 9
_KIJUN_P    = 26
_SENKOU_B_P = 52
_DISPLACE   = 26

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["brk_kumo_hi", "brk_kumo_lo"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "brk_kumo_hi": (
        "**Ichimoku kumo (cloud) upside breakout** — "
        "today's LOW closed above the visible cloud top "
        "(max of senkou A / senkou B as drawn at bar T, computed from "
        "data ending 26 bars prior) while yesterday's LOW was at or "
        "below the cloud top.  Strict whole-bar breakout — no intraday "
        "retracement into the cloud."
    ),
    "brk_kumo_lo": (
        "**Ichimoku kumo (cloud) downside breakdown** — "
        "today's HIGH closed below the visible cloud bottom "
        "(min of senkou A / senkou B as drawn at bar T) while yesterday's "
        "HIGH was at or above the cloud bottom.  Strict whole-bar "
        "breakdown — no intraday retracement into the cloud."
    ),
}


class BrkKumoDetector:
    """Initialise once per stock cache; call detect() per bar.

    Parameters
    ----------
    stock_cache:
        Hourly or daily bar cache for the stock.
    side:
        "hi" → fire on close crossing above kumo top
        "lo" → fire on close crossing below kumo bottom
    """

    def __init__(self, stock_cache: DataCache, side: str = "hi",
                 gate_lookback: int = 1) -> None:
        if side not in ("hi", "lo"):
            raise ValueError(f"side must be 'hi' or 'lo', got {side!r}")
        if gate_lookback < 1:
            raise ValueError(f"gate_lookback must be ≥1, got {gate_lookback}")
        self._side          = side
        self._gate_lookback = gate_lookback
        self._sign_type     = f"brk_kumo_{side}"
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
                if b.low < date_lo[d]:
                    date_lo[d] = b.low
            date_cl[d] = b.close

        trading_dates = sorted(date_hi)
        if len(trading_dates) < _SENKOU_B_P + _DISPLACE + 2:
            self._fire_events: list[tuple[int, float]] = []
            return

        highs_arr = np.array([date_hi[d] for d in trading_dates], dtype=float)
        lows_arr  = np.array([date_lo[d] for d in trading_dates], dtype=float)

        ichi = calc_ichimoku(
            [date_hi[d] for d in trading_dates],
            [date_lo[d] for d in trading_dates],
            [date_cl[d] for d in trading_dates],
            tenkan_period=_TENKAN_P,
            kijun_period=_KIJUN_P,
            senkou_b_period=_SENKOU_B_P,
            displacement=_DISPLACE,
        )
        senkou_a = np.array(ichi["senkou_a"], dtype=float)
        senkou_b = np.array(ichi["senkou_b"], dtype=float)
        d        = int(ichi["displacement"])

        n = len(trading_dates)
        # Level visible at bar T = (max|min)(SSA[T-d], SSB[T-d])
        level = np.full(n, np.nan)
        if side == "hi":
            level[d:] = np.maximum(senkou_a[: n - d], senkou_b[: n - d])
        else:
            level[d:] = np.minimum(senkou_a[: n - d], senkou_b[: n - d])

        # Strict whole-bar fire: hi uses LOW, lo uses HIGH
        edge = lows_arr if side == "hi" else highs_arr
        K    = gate_lookback

        fire_events: list[tuple[int, float]] = []
        for T in range(d + K, n):
            lv = level[T]
            if np.isnan(lv) or lv <= 0:
                continue
            # Today must cross the level
            if side == "hi":
                if not (edge[T] > lv):
                    continue
            else:
                if not (edge[T] < lv):
                    continue
            # All K prior bars must have been on the opposite side
            prior_below = True
            for i in range(1, K + 1):
                lv_i = level[T - i]
                if np.isnan(lv_i) or lv_i <= 0:
                    prior_below = False; break
                if side == "hi":
                    if not (edge[T - i] <= lv_i):
                        prior_below = False; break
                else:
                    if not (edge[T - i] >= lv_i):
                        prior_below = False; break
            if not prior_below:
                continue
            crossed = True
            edge_gap = ((edge[T] - lv) if side == "hi" else (lv - edge[T])) / lv
            if not crossed:
                continue
            fire_date = trading_dates[T]
            bar_idx = date_first_bar_idx.get(fire_date)
            if bar_idx is None:
                continue
            score = float(min(edge_gap, 0.05) / 0.05)
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
                sign_type=self._sign_type,
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
