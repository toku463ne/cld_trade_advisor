"""corr_shift — Overseas Correlation Crossover sign detector.

Fires on the first hourly bar of a trading day when, over the preceding
``delta_window`` daily bars:
  - Δcorr(stock, ^N225)  < −DELTA_MIN   (domestic coupling weakening)
  - Δcorr(stock, ^GSPC)  > +DELTA_MIN   (overseas coupling strengthening)

The corr series are loaded externally from the moving_corr table and passed
in as pd.Series (ts → corr_value) at daily granularity.  The stock 1h cache
is used only for timestamps / first-hourly-bar mapping.

Score = min((-Δn225 + Δgspc) / (2 × DELTA_MIN), 2.0) / 2.0
  Both legs of the crossover contribute; saturates at 1.0 when each
  delta equals DELTA_MIN.

Valid for up to ``valid_bars`` *hourly bars* after firing (time-bounded only).
"""

from __future__ import annotations

import bisect
import datetime

import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_DELTA_MIN    = 0.15
_DELTA_WINDOW = 5


class CorrShiftDetector:
    """Initialise with stock 1h cache + two pre-loaded daily corr Series."""

    def __init__(
        self,
        stock_cache: DataCache,
        n225_corr: pd.Series,   # ts (tz-aware datetime) → rolling corr vs ^N225, 1d
        gspc_corr: pd.Series,   # ts (tz-aware datetime) → rolling corr vs ^GSPC, 1d
        delta_window: int  = _DELTA_WINDOW,
        delta_min: float   = _DELTA_MIN,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        # date → first hourly bar index
        date_to_first: dict[datetime.date, int] = {}
        for i, dt in enumerate(self._dts):
            d = dt.date()
            if d not in date_to_first:
                date_to_first[d] = i

        # 5-day delta for each corr series
        n225_delta = n225_corr - n225_corr.shift(delta_window)
        gspc_delta = gspc_corr - gspc_corr.shift(delta_window)

        self._fire_events: list[tuple[int, float]] = []
        for ts in n225_delta.index:
            nd = n225_delta.get(ts)
            gd = gspc_delta.get(ts)
            if nd is None or gd is None:
                continue
            nd, gd = float(nd), float(gd)
            if pd.isna(nd) or pd.isna(gd):
                continue
            if nd < -delta_min and gd > delta_min:
                d = ts.date() if hasattr(ts, "date") else ts
                if d not in date_to_first:
                    continue
                cross_strength = min((-nd + gd) / (2.0 * delta_min), 2.0) / 2.0
                self._fire_events.append((date_to_first[d], cross_strength))

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
                sign_type="corr_shift",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
