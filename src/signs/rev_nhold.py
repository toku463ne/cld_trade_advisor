"""rev_nhold — Resilient stock at confirmed N225 trough. See docs/signs/rev_nhold.md."""

from __future__ import annotations

import bisect
import datetime

from src.indicators.zigzag import detect_peaks
from src.signs.base import SignResult
from src.simulator.cache import DataCache

_N225_DD_MIN        = -0.10   # N225 must have fallen at least 10 % over the leg
_STOCK_DD_MAX_NEG   = -0.03   # stock close-to-close drawdown must be ≥ −3 %
_LOOKBACK_DAYS      =   20    # window for the "no fresh 20-day low" check
_N225_DEPTH_CAP     =  0.20   # normalisation cap for the n225_depth_bonus
_ZZ_SIZE            =    5
_ZZ_MID_SIZE        =    2

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["rev_nhold"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "rev_nhold": (
        "**Resilient at N225 Trough** — "
        "N225 confirms a deep trough (≥10 % drop) but the stock barely fell "
        "(≥ −3 %) and didn't make a new 20-day low. Strongest names lead the rebound."
    ),
}


class RevNholdDetector:
    """Initialise once per (stock, N225) cache pair; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        n225_cache:  DataCache,
        n225_dd_min:      float = _N225_DD_MIN,
        stock_dd_max_neg: float = _STOCK_DD_MAX_NEG,
        lookback_days:    int   = _LOOKBACK_DAYS,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        # ── Daily stock series (close + low) ──────────────────────────────────
        stock_close: dict[datetime.date, float] = {}
        stock_low:   dict[datetime.date, float] = {}
        for b in stock_cache.bars:
            d = b.dt.date()
            stock_close[d] = b.close                           # last-bar close wins
            stock_low[d]   = min(stock_low.get(d, float("inf")), b.low)
        stock_dates_sorted = sorted(stock_close)

        # ── Daily N225 series (close + intraday H/L for zigzag) ───────────────
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

        peaks     = detect_peaks(n225_highs, n225_lows, size=_ZZ_SIZE, middle_size=_ZZ_MID_SIZE)
        confirmed = [p for p in peaks if abs(p.direction) == 2]

        # ── Stock bar-index helpers (for fire-event timestamps) ───────────────
        self._trading_dates: list[datetime.date] = sorted({dt.date() for dt in self._dts})
        date_to_first: dict[datetime.date, int] = {}
        date_to_last:  dict[datetime.date, int] = {}
        for i, dt in enumerate(self._dts):
            d = dt.date()
            if d not in date_to_first:
                date_to_first[d] = i
            date_to_last[d] = i
        self._date_to_last = date_to_last

        # ── Scan confirmed N225 LOWs ─────────────────────────────────────────
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

            # ── N225 drawdown gate ───────────────────────────────────────────
            n225_hp = n225_close.get(high_date)
            n225_lp = n225_close.get(low_date)
            if n225_hp is None or n225_lp is None or n225_hp == 0:
                continue
            n225_dd = (n225_lp - n225_hp) / n225_hp        # negative
            if n225_dd > n225_dd_min:
                continue                                    # decline not deep enough

            # ── Stock drawdown gate ──────────────────────────────────────────
            stk_hp = stock_close.get(high_date)
            stk_lp = stock_close.get(low_date)
            if stk_hp is None or stk_lp is None or stk_hp == 0:
                continue
            stk_dd = (stk_lp - stk_hp) / stk_hp
            if stk_dd < stock_dd_max_neg:
                continue                                    # stock fell too hard — capitulation

            # ── No fresh N-day low gate ──────────────────────────────────────
            i_low = bisect.bisect_left(stock_dates_sorted, low_date)
            if i_low <= 0:
                continue
            window_start = max(0, i_low - lookback_days)
            prior_lows   = [
                stock_low[d] for d in stock_dates_sorted[window_start:i_low]
                if d in stock_low
            ]
            if not prior_lows:
                continue
            prior_min_low = min(prior_lows)
            stk_low_today = stock_low.get(low_date)
            if stk_low_today is None or stk_low_today <= prior_min_low:
                continue                                    # made a fresh 20d low

            # ── Confirmation bar (low_bar + ZZ_SIZE N225 trading days) ───────
            confirm_bi = p.bar_index + _ZZ_SIZE
            if confirm_bi >= len(n225_dates):
                continue
            confirm_date = n225_dates[confirm_bi]
            if confirm_date not in date_to_first:
                continue

            # ── Score ────────────────────────────────────────────────────────
            denom = -stock_dd_max_neg          # positive number, e.g. 0.03
            resilience_norm  = max(0.0, min((stk_dd - stock_dd_max_neg) / denom, 1.0))
            n225_depth_bonus = min(abs(n225_dd) / _N225_DEPTH_CAP, 1.0)
            score = 0.6 * resilience_norm + 0.4 * n225_depth_bonus

            self._fire_events.append((date_to_first[confirm_date], confirm_date, score))

    @property
    def fire_events(self) -> list[tuple[int, datetime.date, float]]:
        """Read-only view of (bar_idx, confirm_date, score) fire events."""
        return self._fire_events

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 3,
    ) -> SignResult | None:
        """Return the most recent valid rev_nhold sign at *as_of*, or None.

        valid_bars counts *trading days*. Default 3 matches the strategy's
        post-2026-05 default; the bounce window from a confirmed trough is
        typically 3–5 days.
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
                sign_type="rev_nhold",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
