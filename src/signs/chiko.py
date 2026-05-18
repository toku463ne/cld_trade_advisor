"""chiko — strict-zone version of the ichimoku chiko-span concept.

One detector, both directions via the ``side`` argument:

  side="hi" → SIGN_TYPE "chiko_hi" — recent-5 strictly ABOVE prior-5
  side="lo" → SIGN_TYPE "chiko_lo" — recent-5 strictly BELOW prior-5

Operator spec (2026-05-18 strict-zone choice).  Long fire:

  long_fire[T] =
      min(close[T-4..T]) > max(close[T-30..T-26])
    AND NOT (min(close[T-5..T-1]) > max(close[T-31..T-27]))

Short fire is the mirror with max/min swapped and < comparison.
Built 2026-05-18 per operator request — formalises the ichimoku chiko
(lagging-span) concept "today's price meaningfully different from
26 bars ago" into a strict, low-noise event.

Note: chiko is the only of the 3 new modules NOT switched to
low/high (2026-05-18) — the strict-zone close-comparison is already a
multi-bar interval check (every recent close > every prior close),
which independently achieves the "stage change" semantic.

**Canonical rebench (2026-05-18):**
- chiko_hi: pooled DR 51.3% (FY2018–FY2024, n=7,584), FY2025 OOS
  **60.2%** (n=1,188, p<0.001) — strongest FY2025 OOS of all 6 new
  ichimoku signs.  perm_pass 4/7.
- chiko_lo: pooled DR 51.8% (n=6,257), FY2025 OOS 51.8% (weakest
  FY2025 of the 6), perm_pass 4/7.

**Confluence A/B (`src/analysis/confluence_ichimoku_ab.py`, 2026-05-18):**
After brk_kumo/brk_tenkan switched to strict whole-bar, expanded set
(baseline + 3 _hi signs incl. chiko_hi) tied with baseline at N≥3
(+3.64 vs +3.80, Δ −0.16 within noise) with better mean_r and win%.
Per operator decision, **chiko_hi ADDED to ConfluenceSignStrategy
bullish set** (10 signs total).  chiko_lo remains catalogue-only.
"""
from __future__ import annotations

import bisect
import datetime

import numpy as np
import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_RECENT = 5     # length of recent window
_PRIOR  = 5     # length of prior window
_GAP    = 26    # bars between end of prior window and end of recent window

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["chiko_hi", "chiko_lo"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "chiko_hi": (
        "**Chiko-span strict bullish break** — "
        "every one of the last 5 closes is above every one of the closes "
        "in the 5-bar window ending 26 bars prior, and the bar before "
        "today did not satisfy the same.  Operator-specified formalisation "
        "of the ichimoku chiko-line concept."
    ),
    "chiko_lo": (
        "**Chiko-span strict bearish break** — "
        "every one of the last 5 closes is below every one of the closes "
        "in the 5-bar window ending 26 bars prior, and the bar before "
        "today did not satisfy the same.  Mirror of chiko_hi."
    ),
}


class ChikoDetector:
    """Initialise once per stock cache; call detect() per bar.

    Parameters
    ----------
    stock_cache:
        Hourly or daily bar cache for the stock.
    side:
        "hi" → fire when recent-5 closes are all > prior-5 max
        "lo" → fire when recent-5 closes are all < prior-5 min
    """

    def __init__(self, stock_cache: DataCache, side: str = "hi") -> None:
        if side not in ("hi", "lo"):
            raise ValueError(f"side must be 'hi' or 'lo', got {side!r}")
        self._side       = side
        self._sign_type  = f"chiko_{side}"
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        date_cl:  dict[datetime.date, float] = {}
        date_first_bar_idx: dict[datetime.date, int] = {}
        for i, b in enumerate(stock_cache.bars):
            d = b.dt.date()
            if d not in date_first_bar_idx:
                date_first_bar_idx[d] = i
            date_cl[d] = b.close

        trading_dates = sorted(date_cl)
        if len(trading_dates) < _GAP + _PRIOR + 2:
            self._fire_events: list[tuple[int, float]] = []
            return

        closes = pd.Series([date_cl[d] for d in trading_dates], dtype=float)

        # Recent window: closes[T-4..T]
        recent_min = closes.rolling(_RECENT).min().to_numpy()
        recent_max = closes.rolling(_RECENT).max().to_numpy()

        # Prior window: closes[T-30..T-26] = closes.shift(_GAP).rolling(5) ending at T
        prior_min = closes.shift(_GAP).rolling(_PRIOR).min().to_numpy()
        prior_max = closes.shift(_GAP).rolling(_PRIOR).max().to_numpy()

        n = len(trading_dates)
        # edge[T] = recent boundary (recent_min for hi, recent_max for lo)
        # level[T] = prior boundary (prior_max for hi, prior_min for lo)
        # satisfied[T]: hi → edge > level; lo → edge < level
        satisfied = np.zeros(n, dtype=bool)
        for T in range(n):
            if side == "hi":
                e = recent_min[T]; lv = prior_max[T]
                ok = (not np.isnan(e)) and (not np.isnan(lv)) and lv > 0 and e > lv
            else:
                e = recent_max[T]; lv = prior_min[T]
                ok = (not np.isnan(e)) and (not np.isnan(lv)) and lv > 0 and e < lv
            satisfied[T] = ok

        fire_events: list[tuple[int, float]] = []
        for T in range(1, n):
            if not satisfied[T] or satisfied[T - 1]:
                continue
            if side == "hi":
                edge_gap = (recent_min[T] - prior_max[T]) / prior_max[T]
            else:
                edge_gap = (prior_min[T] - recent_max[T]) / prior_min[T]
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
