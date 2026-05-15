"""rev_nlo — Capitulation bounce after confirmed N225 trough. See docs/signs/rev_nlo.md."""

from __future__ import annotations

import bisect
import datetime

from src.indicators.zigzag import detect_peaks
from src.signs.base import SignResult
from src.simulator.cache import DataCache

_N225_DD_MIN      = -0.05   # N225 must have fallen ≥ 5%
_UNDERPERFORM_MIN =  0.50   # stock must have fallen ≥ 50% as much as N225
_N225_DEPTH_CAP   =  0.20   # normalisation cap for n225_depth_bonus
_DEPTH_SCALE      =  2.0    # stock fell 2× N225 → underperform_norm = 1.0
_ZZ_SIZE          =  5
_ZZ_MID_SIZE      =  2

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["rev_nlo"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "rev_nlo": (
        "**Capitulation Bounce** — "
        "stock's drawdown exceeds UNDERPERFORM_MIN × N225 drawdown and N225 zigzag confirms a LOW. "
        "Oversold stock expected to snap back sharply."
    ),
}


class RevNloDetector:
    """Initialise once per (stock, N225) hourly cache pair; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        n225_cache:  DataCache,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        # Derive daily closes from hourly bars
        stock_close: dict[datetime.date, float] = {}
        for b in stock_cache.bars:
            stock_close[b.dt.date()] = b.close  # last bar of day wins

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
        peaks     = detect_peaks(n225_highs, n225_lows, size=_ZZ_SIZE, middle_size=_ZZ_MID_SIZE)
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

            # Most recent confirmed HIGH before this LOW
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
            n225_dd = (n225_lp - n225_hp) / n225_hp  # negative
            if n225_dd > _N225_DD_MIN:
                continue  # N225 decline not steep enough

            stk_hp = stock_close.get(high_date)
            stk_lp = stock_close.get(low_date)
            if stk_hp is None or stk_lp is None or stk_hp == 0:
                continue
            stk_dd = (stk_lp - stk_hp) / stk_hp

            # Capitulation: stock fell at least UNDERPERFORM_MIN × N225 drawdown
            if abs(stk_dd) < _UNDERPERFORM_MIN * abs(n225_dd):
                continue  # stock held up — str_lead territory, not capitulation

            # Confirmation date = low bar + ZZ_SIZE N225 trading days
            confirm_bi = p.bar_index + _ZZ_SIZE
            if confirm_bi >= len(n225_dates):
                continue
            confirm_date = n225_dates[confirm_bi]
            if confirm_date not in date_to_first:
                continue

            underperform_norm = min(abs(stk_dd) / (abs(n225_dd) * _DEPTH_SCALE), 1.0)
            n225_depth_bonus  = min(abs(n225_dd) / _N225_DEPTH_CAP, 1.0)
            score = underperform_norm * 0.6 + n225_depth_bonus * 0.4

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
        """Return the most recent valid rev_nlo sign at *as_of*, or None."""
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
                sign_type="rev_nlo",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
