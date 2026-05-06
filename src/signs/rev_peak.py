"""rev_peak — Price Near Recent Same-Side Zigzag Peak (Reversal).

Fires on the hourly bar when the bar's tested price is within
``proximity_pct`` of one of the last ``n_peaks`` confirmed same-type
zigzag peaks, from either the hourly or the daily timeframe.

  side='lo'  → test_price = bar.low  near a prior confirmed LOW
               sign_type = "rev_lo"  — expect UP bounce (support test)
  side='hi'  → test_price = bar.high near a prior confirmed HIGH
               sign_type = "rev_hi"  — expect DOWN reversal (resistance test)

Both hourly (zz_size_1h) and daily (zz_size_1d, derived from hourly bars)
peaks contribute to the reference level pool.  Only peaks whose confirmation
has fully passed before the current bar are used — no look-ahead.

Score = 1 − proximity / proximity_pct
  1.0 when price is exactly at the prior peak; 0.0 at the boundary.

Valid for up to ``valid_bars`` bars after firing (time-bounded only).
"""

from __future__ import annotations

import bisect
import datetime

from src.indicators.zigzag import detect_peaks
from src.signs.base import SignResult
from src.simulator.cache import DataCache


class RevPeakDetector:
    """Initialise once per stock hourly cache; call detect() per bar."""

    _ZZ_SIZE_1H = 5
    _ZZ_SIZE_1D = 5
    _ZZ_MID     = 2

    def __init__(
        self,
        stock_cache: DataCache,
        proximity_pct: float = 0.005,
        side: str            = "lo",
        n_peaks: int         = 2,
    ) -> None:
        assert side in ("lo", "hi"), "side must be 'lo' or 'hi'"
        self._stock_code   = stock_cache.stock_code
        self._side         = side
        self._proximity    = proximity_pct
        bars               = stock_cache.bars
        self._dts          = [b.dt for b in bars]
        self._sign_type    = "rev_lo" if side == "lo" else "rev_hi"

        target_dir = -2 if side == "lo" else 2

        # ── Hourly zigzag ────────────────────────────────────────────────────
        highs_1h = [b.high for b in bars]
        lows_1h  = [b.low  for b in bars]
        peaks_1h = detect_peaks(highs_1h, lows_1h,
                                size=self._ZZ_SIZE_1H, middle_size=self._ZZ_MID)

        # obs_peaks: (observable_from_bar_idx, formation_bar_idx, price)
        obs_peaks: list[tuple[int, int, float]] = []
        for p in peaks_1h:
            if p.direction != target_dir:
                continue
            obs_from = p.bar_index + self._ZZ_SIZE_1H
            if obs_from >= len(bars):
                continue
            obs_peaks.append((obs_from, p.bar_index, p.price))

        # ── Daily zigzag (derived from hourly bars) ───────────────────────
        date_bars: dict[datetime.date, list[tuple[int, object]]] = {}
        for i, b in enumerate(bars):
            date_bars.setdefault(b.dt.date(), []).append((i, b))

        daily_dates = sorted(date_bars)
        daily_highs = [max(b.high for _, b in date_bars[d]) for d in daily_dates]
        daily_lows  = [min(b.low  for _, b in date_bars[d]) for d in daily_dates]

        peaks_1d = detect_peaks(daily_highs, daily_lows,
                                size=self._ZZ_SIZE_1D, middle_size=self._ZZ_MID)

        # date → first / last hourly bar index
        d_to_first: dict[datetime.date, int] = {}
        d_to_last:  dict[datetime.date, int] = {}
        for i, b in enumerate(bars):
            d = b.dt.date()
            if d not in d_to_first:
                d_to_first[d] = i
            d_to_last[d] = i

        for p in peaks_1d:
            if p.direction != target_dir:
                continue
            obs_daily_bi = p.bar_index + self._ZZ_SIZE_1D
            if obs_daily_bi >= len(daily_dates):
                continue
            obs_date    = daily_dates[obs_daily_bi]
            obs_from    = d_to_first.get(obs_date)
            if obs_from is None:
                continue
            peak_date     = daily_dates[p.bar_index]
            formation_idx = d_to_last.get(peak_date, obs_from)
            obs_peaks.append((obs_from, formation_idx, p.price))

        # Sort by (observable_from) for efficient scanning
        obs_peaks.sort(key=lambda x: x[0])
        self._obs_peaks  = obs_peaks
        self._n_peaks    = n_peaks

        self._fire_events: list[tuple[int, float]] = self._scan(bars)

    def _scan(self, bars: list) -> list[tuple[int, float]]:
        events: list[tuple[int, float]] = []
        ptr = 0
        known: list[tuple[int, float]] = []

        for idx, bar in enumerate(bars):
            while ptr < len(self._obs_peaks) and self._obs_peaks[ptr][0] <= idx:
                _, formation_idx, price = self._obs_peaks[ptr]
                bisect.insort(known, (formation_idx, price))
                ptr += 1

            if not known or idx == 0:
                continue

            # Directional approach filter: price must be moving toward the level.
            # rev_lo: current bar's close < open (declining into support)
            # rev_hi: current bar's close > open (rising into resistance)
            if self._side == "lo" and bar.close >= bar.open:
                continue
            if self._side == "hi" and bar.close <= bar.open:
                continue

            recent = known[-self._n_peaks:]
            test_price = bar.low if self._side == "lo" else bar.high
            if not test_price:
                continue

            for _, peak_price in reversed(recent):
                if not peak_price:
                    continue
                proximity = abs(test_price - peak_price) / peak_price
                if proximity <= self._proximity:
                    score = 1.0 - proximity / self._proximity
                    events.append((idx, score))
                    break

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
            valid_until_idx = min(fi + valid_bars, len(self._dts) - 1)
            return SignResult(
                sign_type=self._sign_type,
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
