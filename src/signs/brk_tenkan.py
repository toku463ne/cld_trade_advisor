"""brk_tenkan — LOW (or HIGH) crosses the Ichimoku tenkan (conversion) line.

One detector, both directions via the ``side`` argument:

  side="hi" → SIGN_TYPE "brk_tenkan_hi" — LOW crosses ABOVE tenkan[T]
  side="lo" → SIGN_TYPE "brk_tenkan_lo" — HIGH crosses BELOW tenkan[T]

  tenkan[T] = (max(H[T-8..T]) + min(L[T-8..T])) / 2   (9-bar midpoint)

Operator preference (2026-05-18): use **low** for hi-side and **high**
for lo-side, NOT close.  Matches brk_wall / brk_floor convention —
strict whole-bar breakout, no intraday retracement through the level.

Transition-gated fire (prior bar on the opposite side).  Tenkan is a
fast line so fire rate is high — main intended use is confluence /
situational picture, not standalone entry trigger.

Built 2026-05-18 per operator request for situational picture signs.

**Canonical rebench (2026-05-18, strict whole-bar version):**
- brk_tenkan_hi: pooled DR ~52% (FY2018–FY2024, n=~23k), FY2025 OOS
  57.2% (n=4,284, p<0.001), perm_pass 5/7 (strongest of the 6 new signs)
- brk_tenkan_lo: pooled DR ~52%, FY2025 OOS 54.8% (n=3,636, p<0.001),
  perm_pass 4/7.

**Confluence A/B (`src/analysis/confluence_ichimoku_ab.py`, 2026-05-18):**
After switching to strict whole-bar interpretation, expanded set
(baseline + 3 new _hi signs) tied with baseline at N≥3 (+3.64 vs +3.80,
Δ −0.16 within noise) with better mean_r and win%.  Per operator
decision, **brk_tenkan_hi ADDED to ConfluenceSignStrategy bullish
set** (10 signs total).  brk_tenkan_lo remains catalogue-only.
"""
from __future__ import annotations

import bisect
import datetime

import numpy as np

from src.indicators.ichimoku import calc_ichimoku
from src.signs.base import SignResult
from src.simulator.cache import DataCache

_TENKAN_P   = 9
_KIJUN_P    = 26   # unused but required by calc_ichimoku
_SENKOU_B_P = 52   # unused but required by calc_ichimoku
_DISPLACE   = 26

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["brk_tenkan_hi", "brk_tenkan_lo"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "brk_tenkan_hi": (
        "**Tenkan-sen upside breakout** — "
        "today's LOW moved above the 9-bar tenkan line "
        "((max H9 + min L9)/2) while yesterday's LOW was at or below.  "
        "Strict whole-bar breakout — no intraday retracement through "
        "the tenkan line."
    ),
    "brk_tenkan_lo": (
        "**Tenkan-sen downside breakdown** — "
        "today's HIGH moved below the 9-bar tenkan line while yesterday's "
        "HIGH was at or above.  Mirror of brk_tenkan_hi.  Strict "
        "whole-bar breakdown."
    ),
}


class BrkTenkanDetector:
    """Initialise once per stock cache; call detect() per bar."""

    def __init__(self, stock_cache: DataCache, side: str = "hi",
                 gate_lookback: int = 1) -> None:
        if side not in ("hi", "lo"):
            raise ValueError(f"side must be 'hi' or 'lo', got {side!r}")
        if gate_lookback < 1:
            raise ValueError(f"gate_lookback must be ≥1, got {gate_lookback}")
        self._side          = side
        self._gate_lookback = gate_lookback
        self._sign_type     = f"brk_tenkan_{side}"
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
        if len(trading_dates) < _TENKAN_P + 2:
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
        tenkan = np.array(ichi["tenkan"], dtype=float)

        # Strict whole-bar fire: hi uses LOW, lo uses HIGH
        edge = lows_arr if side == "hi" else highs_arr
        K    = gate_lookback

        n = len(trading_dates)
        fire_events: list[tuple[int, float]] = []
        for T in range(K, n):
            tk = tenkan[T]
            if np.isnan(tk) or tk <= 0:
                continue
            if side == "hi":
                if not (edge[T] > tk):
                    continue
            else:
                if not (edge[T] < tk):
                    continue
            prior_below = True
            for i in range(1, K + 1):
                tk_i = tenkan[T - i]
                if np.isnan(tk_i) or tk_i <= 0:
                    prior_below = False; break
                if side == "hi":
                    if not (edge[T - i] <= tk_i):
                        prior_below = False; break
                else:
                    if not (edge[T - i] >= tk_i):
                        prior_below = False; break
            if not prior_below:
                continue
            crossed = True
            edge_gap = ((edge[T] - tk) if side == "hi" else (tk - edge[T])) / tk
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
