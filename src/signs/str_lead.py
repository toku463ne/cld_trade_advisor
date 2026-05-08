"""str_lead — Post-N225-Bottom Leader sign detector.

Fires on the first hourly bar of the day when the N225 zigzag CONFIRMS a LOW,
provided the stock's drawdown during the preceding N225 decline is less than
OUTPERFORM_MAX × |N225 drawdown|.

Daily highs/lows/closes are derived internally from the hourly caches (same
pattern as str_hold), so the detector accepts 1 h caches throughout.

Conditions:
  - N225 zigzag confirms a LOW (direction = −2) at bar T
  - |N225 drawdown from prior confirmed HIGH to T| ≥ N225_DD_MIN
  - |stock drawdown over same window| < OUTPERFORM_MAX × |N225 drawdown|

Score = outperform_ratio × 0.6 + n225_depth_bonus × 0.2 + corr_bonus × 0.2
  outperform_ratio = 1 − |stock_dd| / |n225_dd|        [0..1, 1 = stock was flat]
  n225_depth_bonus = min(|n225_dd| / 0.20, 1.0)        [deeper correction = more meaningful]
  corr_bonus       = max(0, moving_corr vs ^N225 at confirm_date, 1h window_bars=100)
                     [stock that normally tracks N225 but held up = stronger signal]

The rolling correlation is loaded externally from the moving_corr table and passed
in as a ``corr_n225_1h`` mapping of date → corr_value.  When absent (None or missing
key), the term defaults to 0.0 (neutral — no bonus, no penalty).

Valid for up to ``valid_bars`` *trading days* after firing (time-bounded only).
"""
# ── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
# run_id=25  n=405  direction_rate=59.5%  p<0.001
# bench_flw=0.047  bench_rev=0.024  mean_bars=11.6
# → 2-year result was RECOMMEND but coincided with sustained bull market (FY2023+FY2024).
#
# ── 7-year cross-validation (FY2018–FY2024, prior-year cluster sets) ──
# pooled DR=47.2%  p≈0.062  perm_pass=3/7
# FY breakdown: FY2018=60.2% (bull), FY2019=26.6%, FY2020=31.1%, FY2021=46.2%,
#               FY2022=36.6%, FY2023=68.4% (bull), FY2024=62.0% (bull)
# → CAUTION: behaves as a reversal sign in non-bull years. Only use when N225 is in
#   a confirmed bull trend (last confirmed zigzag peak is a LOW). In neutral/bear years
#   DR drops well below 50% — the capitulation-to-leadership thesis fails when N225
#   makes multiple false bottoms.

from __future__ import annotations

import bisect
import datetime

from src.indicators.zigzag import detect_peaks
from src.signs.base import SignResult
from src.simulator.cache import DataCache

_N225_DD_MIN     = -0.05   # N225 must have fallen ≥ 5 % during the window
_OUTPERFORM_MAX  =  0.50   # stock drawdown < 50 % of N225 drawdown
_N225_DEPTH_CAP  =  0.20   # cap for n225_depth_bonus normalisation
_ZZ_SIZE         =  5
_ZZ_MID_SIZE     =  2


class StrLeadDetector:
    """Initialise once per (stock, N225) hourly cache pair; call detect() per bar."""

    def __init__(
        self,
        stock_cache:   DataCache,
        n225_cache:    DataCache,
        corr_n225_1h:  dict[datetime.date, float] | None = None,
    ) -> None:
        self._stock_code  = stock_cache.stock_code
        self._dts         = [b.dt for b in stock_cache.bars]
        self._corr_n225   = corr_n225_1h or {}

        # Derive daily close / high / low from hourly bars
        stock_close: dict[datetime.date, float] = {}
        for b in stock_cache.bars:
            stock_close[b.dt.date()] = b.close   # last bar of day wins

        n225_close: dict[datetime.date, float] = {}
        n225_high:  dict[datetime.date, float] = {}
        n225_low:   dict[datetime.date, float] = {}
        for b in n225_cache.bars:
            d = b.dt.date()
            n225_close[d] = b.close
            n225_high[d]  = max(n225_high.get(d, 0.0), b.high)
            n225_low[d]   = min(n225_low.get(d, float("inf")), b.low)

        n225_dates = sorted(n225_high)
        n225_highs = [n225_high[d] for d in n225_dates]
        n225_lows  = [n225_low[d]  for d in n225_dates]

        # Zigzag on N225 daily bars
        peaks = detect_peaks(n225_highs, n225_lows, size=_ZZ_SIZE, middle_size=_ZZ_MID_SIZE)
        confirmed = [p for p in peaks if abs(p.direction) == 2]

        # Hourly-bar lookup helpers
        self._trading_dates: list[datetime.date] = sorted({dt.date() for dt in self._dts})
        date_to_first: dict[datetime.date, int] = {}
        date_to_last:  dict[datetime.date, int] = {}
        for i, dt in enumerate(self._dts):
            d = dt.date()
            if d not in date_to_first:
                date_to_first[d] = i
            date_to_last[d] = i
        self._date_to_last = date_to_last

        # Scan confirmed N225 LOWs
        self._fire_events: list[tuple[int, datetime.date, float]] = []
        for j, p in enumerate(confirmed):
            if p.direction != -2:
                continue

            # Find the most recent confirmed HIGH before this LOW
            prior_high = None
            for k in range(j - 1, -1, -1):
                if confirmed[k].direction == 2:
                    prior_high = confirmed[k]
                    break
            if prior_high is None:
                continue

            low_date  = n225_dates[p.bar_index]
            high_date = n225_dates[prior_high.bar_index]

            n225_hp = n225_close.get(high_date)
            n225_lp = n225_close.get(low_date)
            if n225_hp is None or n225_lp is None or n225_hp == 0:
                continue
            n225_dd = (n225_lp - n225_hp) / n225_hp   # negative
            if n225_dd > _N225_DD_MIN:
                continue   # N225 decline not steep enough

            stk_hp = stock_close.get(high_date)
            stk_lp = stock_close.get(low_date)
            if stk_hp is None or stk_lp is None or stk_hp == 0:
                continue
            stk_dd = (stk_lp - stk_hp) / stk_hp

            if abs(stk_dd) >= _OUTPERFORM_MAX * abs(n225_dd):
                continue   # stock fell too much relative to N225

            # Confirmation date = low bar + ZZ_SIZE N225 trading days
            confirm_bi = p.bar_index + _ZZ_SIZE
            if confirm_bi >= len(n225_dates):
                continue
            confirm_date = n225_dates[confirm_bi]
            if confirm_date not in date_to_first:
                continue

            outperform_ratio = 1.0 - abs(stk_dd) / abs(n225_dd)  # 0..1
            n225_depth_bonus = min(abs(n225_dd) / _N225_DEPTH_CAP, 1.0)

            # Recent 1h rolling corr vs ^N225 (window_bars=100) at confirm_date.
            # Falls back to 0.0 (neutral) when the date is outside the loaded range.
            raw_corr   = self._corr_n225.get(confirm_date, 0.0)
            corr_bonus = max(0.0, raw_corr)

            score = outperform_ratio * 0.6 + n225_depth_bonus * 0.2 + corr_bonus * 0.2

            self._fire_events.append((date_to_first[confirm_date], confirm_date, score))

    @property
    def fire_events(self) -> list[tuple[int, datetime.date, float]]:
        """Read-only view of (bar_idx, confirm_date, score) fire events."""
        return self._fire_events

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 5,
    ) -> SignResult | None:
        """Return the most recent valid str_lead sign at *as_of*, or None.

        valid_bars counts *trading days*.
        """
        idx = bisect.bisect_right(self._dts, as_of) - 1
        if idx < 0 or not self._fire_events:
            return None

        as_of_date     = as_of.date()
        as_of_date_pos = bisect.bisect_right(self._trading_dates, as_of_date) - 1

        for fi, fire_date, score in reversed(self._fire_events):
            if fi > idx:
                continue
            fire_date_pos        = bisect.bisect_left(self._trading_dates, fire_date)
            trading_days_elapsed = as_of_date_pos - fire_date_pos
            if trading_days_elapsed > valid_bars:
                break

            valid_date_pos  = min(fire_date_pos + valid_bars, len(self._trading_dates) - 1)
            valid_date      = self._trading_dates[valid_date_pos]
            valid_until_idx = self._date_to_last.get(valid_date, idx)

            return SignResult(
                sign_type="str_lead",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
