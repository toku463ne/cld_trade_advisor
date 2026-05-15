"""str_hold — Multi-day Relative Strength During Decline sign detector. See docs/signs/str_hold.md."""

from __future__ import annotations

import bisect
import datetime

import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_N225_5D_MAX         = -0.02
_STOCK_5D_MIN        = -0.005
_MIN_DAYS_OUTPERFORM =  3

_REL_GAP_CAP = 0.05

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["str_hold"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "str_hold": (
        "**Relative Strength During Decline** — "
        "stock stays flat (≤0.5% drop) while N225 drops ≥2% over 5 days. "
        "Implies steady buying that absorbs the broad market fall."
    ),
}


class StrHoldDetector:
    """Initialise once per (stock, N225) *hourly* cache pair; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        n225_cache: DataCache,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        # Derive daily close = last hourly close of each date (UTC date = JST trade date)
        stock_by_date: dict[datetime.date, float] = {}
        for b in stock_cache.bars:
            stock_by_date[b.dt.date()] = b.close

        n225_by_date: dict[datetime.date, float] = {}
        for b in n225_cache.bars:
            n225_by_date[b.dt.date()] = b.close

        common_dates = sorted(set(stock_by_date) & set(n225_by_date))

        stock_s = pd.Series(
            [stock_by_date[d] for d in common_dates], index=common_dates, dtype=float
        )
        n225_s = pd.Series(
            [n225_by_date[d] for d in common_dates], index=common_dates, dtype=float
        )

        stock_1d = stock_s.pct_change()
        n225_1d  = n225_s.pct_change()
        stock_5d = stock_s.pct_change(5)
        n225_5d  = n225_s.pct_change(5)

        # Trading-date list for validity counting (derived from actual hourly bars)
        self._trading_dates: list[datetime.date] = sorted({dt.date() for dt in self._dts})

        # date → first / last hourly bar index
        date_to_first: dict[datetime.date, int] = {}
        date_to_last:  dict[datetime.date, int] = {}
        for i, dt in enumerate(self._dts):
            d = dt.date()
            if d not in date_to_first:
                date_to_first[d] = i
            date_to_last[d] = i
        self._date_to_last = date_to_last

        # Scan: fire on first hourly bar of each qualifying day
        self._fire_events: list[tuple[int, datetime.date, float]] = []
        for i in range(5, len(common_dates)):
            d = common_dates[i]
            if d not in date_to_first:
                continue

            s5 = float(stock_5d.iloc[i])
            n5 = float(n225_5d.iloc[i])
            if pd.isna(s5) or pd.isna(n5):
                continue
            if n5 >= _N225_5D_MAX or s5 <= _STOCK_5D_MIN:
                continue

            consistent_days = sum(
                1 for j in range(i - 4, i + 1)
                if not pd.isna(stock_1d.iloc[j]) and not pd.isna(n225_1d.iloc[j])
                and float(stock_1d.iloc[j]) >= float(n225_1d.iloc[j])
            )
            if consistent_days < _MIN_DAYS_OUTPERFORM:
                continue

            rel_gap_norm   = min((s5 - n5) / _REL_GAP_CAP, 1.0)
            consistency_sc = consistent_days / 5.0
            score = rel_gap_norm * 0.6 + consistency_sc * 0.4

            self._fire_events.append((date_to_first[d], d, score))

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 3,
    ) -> SignResult | None:
        """Return the most recent valid str_hold sign at *as_of*, or None.

        valid_bars counts *trading days* (not hourly bars).
        """
        idx = bisect.bisect_right(self._dts, as_of) - 1
        if idx < 0 or not self._fire_events:
            return None

        as_of_date     = as_of.date()
        as_of_date_pos = bisect.bisect_right(self._trading_dates, as_of_date) - 1

        for fi, fire_date, score in reversed(self._fire_events):
            if fi > idx:
                continue
            fire_date_pos       = bisect.bisect_left(self._trading_dates, fire_date)
            trading_days_elapsed = as_of_date_pos - fire_date_pos
            if trading_days_elapsed > valid_bars:
                break

            valid_date_pos  = min(fire_date_pos + valid_bars, len(self._trading_dates) - 1)
            valid_date      = self._trading_dates[valid_date_pos]
            valid_until_idx = self._date_to_last.get(valid_date, idx)

            return SignResult(
                sign_type="str_hold",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )

        return None
