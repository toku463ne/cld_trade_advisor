"""corr_peak — Peak Correlation B-Metric Alignment sign detector.

Fires on the first hourly bar of the day when N225 zigzag confirms a new LOW,
for stocks where the peak-correlation B-metric vs ^N225 DOWN peaks is negative.

A negative B-metric means the stock historically *rises* in the window after
a confirmed N225 low — making it a natural buy candidate at each N225 bottom.

Conditions:
  - ``n225_down_corr_b`` < 0  (stock tends to rise after N225 confirmed lows)
  - N225 zigzag just confirmed a LOW (direction = −2)

Score = min(−n225_down_corr_b, 1.0)
  More negative B → higher confidence → higher score.

Valid for up to ``valid_bars`` *hourly bars* after firing (time-bounded only).
"""

from __future__ import annotations

import bisect
import datetime

from src.indicators.zigzag import detect_peaks
from src.signs.base import SignResult
from src.simulator.cache import DataCache

_ZZ_SIZE     = 5
_ZZ_MID_SIZE = 2


class CorrPeakDetector:
    """Initialise once per (stock, N225) hourly cache pair + scalar B-metric."""

    def __init__(
        self,
        stock_cache: DataCache,
        n225_cache: DataCache,
        n225_down_corr_b: float,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        # Positive B means stock falls after N225 bottoms — not a buy signal
        if n225_down_corr_b >= 0:
            self._fire_events: list[tuple[int, float]] = []
            return

        # Derive N225 daily high / low from hourly bars
        n225_high: dict[datetime.date, float] = {}
        n225_low:  dict[datetime.date, float] = {}
        for b in n225_cache.bars:
            d = b.dt.date()
            n225_high[d] = max(n225_high.get(d, 0.0), b.high)
            n225_low[d]  = min(n225_low.get(d, float("inf")), b.low)

        n225_dates = sorted(n225_high)
        n225_highs = [n225_high[d] for d in n225_dates]
        n225_lows  = [n225_low[d]  for d in n225_dates]

        peaks = detect_peaks(n225_highs, n225_lows, size=_ZZ_SIZE, middle_size=_ZZ_MID_SIZE)

        date_to_first: dict[datetime.date, int] = {}
        for i, dt in enumerate(self._dts):
            d = dt.date()
            if d not in date_to_first:
                date_to_first[d] = i

        base_score = min(-n225_down_corr_b, 1.0)

        self._fire_events = []
        for p in peaks:
            if p.direction != -2:
                continue
            confirm_bi = p.bar_index + _ZZ_SIZE
            if confirm_bi >= len(n225_dates):
                continue
            confirm_date = n225_dates[confirm_bi]
            if confirm_date not in date_to_first:
                continue
            self._fire_events.append((date_to_first[confirm_date], base_score))

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
                sign_type="corr_peak",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
