"""str_lag — Delayed-trough follower after N225 confirmed low. See docs/signs/str_lag.md."""

from __future__ import annotations

import bisect
import datetime

from src.indicators.zigzag import detect_peaks
from src.signs.base import SignResult
from src.simulator.cache import DataCache

_N225_ZZ_SIZE        =  3     # bars each side for N225 confirmed low
_STOCK_ZZ_SIZE       =  5     # bars each side for stock early trough (5 = meaningful peak)
_STOCK_ZZ_MID        =  2     # right-window for early trough (fire day = trough + 2)
_LAG_MIN             =  _N225_ZZ_SIZE   # = 3; ensures N225 low is knowable in real time
_LAG_MAX             =  7     # beyond 7 bars the signal degrades sharply
_N225_RECOVERY_MAX   =  0.05  # 5 % — gate out cases where N225 has already rallied hard

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["str_lag"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "str_lag": (
        "**Delayed Trough Follower** — "
        "stock has not recovered after N225 confirmed its trough. "
        "Catch-up rally expected once the stock re-enters the uptrend."
    ),
}


class StrLagDetector:
    """Initialise once per (stock, N225) hourly-cache pair; call detect() per bar.

    Parameters
    ----------
    stock_cache:
        1h OHLCV cache for the target stock.
    n225_cache:
        1h OHLCV cache for ^N225.
    corr_n225_1d:
        Optional mapping ``date → 20-bar daily rolling corr vs ^N225``.
        When provided the corr_score component of the score is populated;
        otherwise it defaults to 0 (neutral, no bonus/penalty).
    """

    def __init__(
        self,
        stock_cache:  DataCache,
        n225_cache:   DataCache,
        corr_n225_1d: dict[datetime.date, float] | None = None,
    ) -> None:
        self._stock_code  = stock_cache.stock_code
        self._dts         = [b.dt for b in stock_cache.bars]
        self._corr_n225   = corr_n225_1d or {}

        # ── Build N225 daily bars ─────────────────────────────────────────────
        n225_high:  dict[datetime.date, float] = {}
        n225_low:   dict[datetime.date, float] = {}
        n225_close: dict[datetime.date, float] = {}
        for b in n225_cache.bars:
            d = b.dt.date()
            n225_high[d]  = max(n225_high.get(d,  0.0),          b.high)
            n225_low[d]   = min(n225_low.get(d,   float("inf")), b.low)
            n225_close[d] = b.close

        n225_dates  = sorted(n225_high)
        n225_highs  = [n225_high[d]  for d in n225_dates]
        n225_lows_v = [n225_low[d]   for d in n225_dates]

        # ── N225 confirmed lows (real-time safe) ──────────────────────────────
        # A confirmed peak at index k is *knowable* only from n225_dates[k + N225_ZZ_SIZE].
        n225_peaks = detect_peaks(n225_highs, n225_lows_v, size=_N225_ZZ_SIZE, middle_size=0)

        # List of (low_date, knowable_date, close_at_low), sorted by low_date
        n225_low_events: list[tuple[datetime.date, datetime.date, float]] = []
        # All confirmed peaks (LOW + HIGH), sorted by knowable_date — used by
        # the bull-regime gate ("most recent confirmed peak as of fire_date
        # must be a LOW, not a HIGH").
        n225_peak_events: list[tuple[datetime.date, int]] = []
        for p in n225_peaks:
            if abs(p.direction) != 2:
                continue
            k = p.bar_index
            know_k = k + _N225_ZZ_SIZE
            if know_k >= len(n225_dates):
                continue
            n225_peak_events.append((n225_dates[know_k], p.direction))
            if p.direction == -2:
                close_at_low = n225_close.get(n225_dates[k])
                if close_at_low is None:
                    continue
                n225_low_events.append((n225_dates[k], n225_dates[know_k], close_at_low))
        n225_low_events.sort(key=lambda e: e[0])
        n225_low_dates = [e[0] for e in n225_low_events]
        n225_peak_events.sort(key=lambda e: e[0])
        n225_peak_know_dates = [e[0] for e in n225_peak_events]

        # ── Build stock daily bars ────────────────────────────────────────────
        stock_low:  dict[datetime.date, float] = {}
        stock_high: dict[datetime.date, float] = {}
        for b in stock_cache.bars:
            d = b.dt.date()
            stock_low[d]  = min(stock_low.get(d,  float("inf")), b.low)
            stock_high[d] = max(stock_high.get(d, 0.0),          b.high)

        stock_dates  = sorted(stock_low)
        stock_lows_v = [stock_low[d] for d in stock_dates]
        n_stock      = len(stock_dates)

        # ── Hourly-bar lookup tables ──────────────────────────────────────────
        self._trading_dates: list[datetime.date] = sorted({dt.date() for dt in self._dts})
        date_to_first: dict[datetime.date, int] = {}
        date_to_last:  dict[datetime.date, int] = {}
        for idx, dt in enumerate(self._dts):
            d = dt.date()
            if d not in date_to_first:
                date_to_first[d] = idx
            date_to_last[d] = idx
        self._date_to_last = date_to_last

        # ── Scan for early stock troughs with valid N225 lag ──────────────────
        self._fire_events: list[tuple[int, datetime.date, float]] = []

        for i in range(_STOCK_ZZ_SIZE, n_stock - _STOCK_ZZ_MID):
            # Early trough: local min over [i−ZZ_SIZE … i+ZZ_MID]
            left_min  = min(stock_lows_v[i - _STOCK_ZZ_SIZE : i])
            right_mid = min(stock_lows_v[i + 1 : i + _STOCK_ZZ_MID + 1])

            if stock_lows_v[i] >= left_min or stock_lows_v[i] >= right_mid:
                continue

            trough_date = stock_dates[i]
            fire_date   = stock_dates[i + _STOCK_ZZ_MID]  # i+1

            if fire_date not in date_to_first:
                continue

            # ── Bull-regime gate ──────────────────────────────────────────────
            # Most recent N225 zigzag peak knowable by fire_date must be a LOW.
            # If a confirmed HIGH has appeared since (rally matured), skip.
            pos = bisect.bisect_right(n225_peak_know_dates, fire_date) - 1
            if pos < 0:
                continue
            if n225_peak_events[pos][1] != -2:
                continue

            # ── Find the most recent N225 confirmed low before the trough ─────
            pos = bisect.bisect_left(n225_low_dates, trough_date)
            if pos == 0:
                continue  # no N225 low before this stock trough

            # Walk back to find the latest event whose knowable_date ≤ fire_date
            n225_low_date: datetime.date | None = None
            close_at_n225_low: float | None = None
            for j in range(pos - 1, -1, -1):
                low_d, know_d, close_d = n225_low_events[j]
                if know_d <= fire_date:
                    n225_low_date     = low_d
                    close_at_n225_low = close_d
                    break

            if n225_low_date is None or close_at_n225_low is None:
                continue  # N225 low not yet confirmed by fire time

            # ── Lag in stock bars ─────────────────────────────────────────────
            n225_lag = sum(
                1 for j in range(i)
                if stock_dates[j] > n225_low_date
            )
            if n225_lag < _LAG_MIN or n225_lag > _LAG_MAX:
                continue

            # ── N225 recovery gate ────────────────────────────────────────────
            n225_close_at_trough = n225_close.get(trough_date)
            if n225_close_at_trough is None or close_at_n225_low <= 0:
                continue
            n225_recovery = (n225_close_at_trough - close_at_n225_low) / close_at_n225_low
            if n225_recovery > _N225_RECOVERY_MAX:
                continue

            # ── Score ──────────────────────────────────────────────────────────
            lag_range = _LAG_MAX - _LAG_MIN          # 4
            lag_score = max(0.1, 1.0 - (n225_lag - _LAG_MIN) / lag_range)

            recovery_score = max(0.0, 1.0 - n225_recovery / _N225_RECOVERY_MAX)

            raw_corr   = self._corr_n225.get(trough_date, 0.0) or 0.0
            corr_score = max(0.0, raw_corr)

            score = lag_score * 0.4 + recovery_score * 0.4 + corr_score * 0.2

            self._fire_events.append((date_to_first[fire_date], fire_date, score))

    @property
    def fire_events(self) -> list[tuple[int, datetime.date, float]]:
        """Read-only view of (bar_idx, fire_date, score) fire events."""
        return self._fire_events

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 5,
    ) -> SignResult | None:
        """Return the most recent valid str_lag sign at *as_of*, or None.

        valid_bars counts *trading days* from the fire date.
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
                sign_type="str_lag",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
