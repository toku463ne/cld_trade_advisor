"""corr_shift — Overseas Correlation Crossover sign detector. See docs/signs/corr_shift.md."""

from __future__ import annotations

import bisect
import datetime
import math

import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_DELTA_WINDOW       = 5
_SPREAD_DELTA_MIN   = 0.25   # entry: spread (GSPC−N225) must widen by ≥ this
_EXIT_SPREAD_MAX    = -0.05  # exit: spread drops below this (slight hysteresis past 0)
_PERSIST_DAYS       = 2      # ENTER condition must hold this many consecutive days
_SCORE_K            = 10.0   # logistic steepness for the score

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["corr_shift"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "corr_shift": (
        "**US Correlation Crossover** — "
        "stock's rolling correlation to S&P 500 has risen above its N225 correlation. "
        "Likely driven by a US-side catalyst."
    ),
}


class CorrShiftDetector:
    """Initialise with stock 1h cache + two pre-loaded daily corr Series."""

    def __init__(
        self,
        stock_cache: DataCache,
        n225_corr: pd.Series,   # ts (tz-aware datetime) → rolling corr vs ^N225, 1d
        gspc_corr: pd.Series,   # ts (tz-aware datetime) → rolling corr vs ^GSPC, 1d
        delta_window: int      = _DELTA_WINDOW,
        spread_delta_min: float = _SPREAD_DELTA_MIN,
        exit_spread_max:  float = _EXIT_SPREAD_MAX,
        persist_days:    int   = _PERSIST_DAYS,
        score_k:         float = _SCORE_K,
        score_x0:        float | None = None,
    ) -> None:
        # Logistic midpoint defaults to the bare-entry spread_delta. At that x,
        # score = 0.5; above it climbs smoothly toward 1.0.
        if score_x0 is None:
            score_x0 = spread_delta_min
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        # date → first bar index (= the only bar on daily granularity)
        date_to_first: dict[datetime.date, int] = {}
        for i, dt in enumerate(self._dts):
            d = dt.date()
            if d not in date_to_first:
                date_to_first[d] = i

        # Combined spread + its delta_window-day delta. The single-Δgspc check
        # (gd > 0) still uses the per-leg series.
        spread       = gspc_corr - n225_corr
        spread_delta = spread - spread.shift(delta_window)
        gspc_delta   = gspc_corr - gspc_corr.shift(delta_window)

        # State machine: only fire on (out → in) transitions, with N-day
        # persistence on entry and hysteresis on exit (spread must drop past
        # exit_spread_max to leave the regime).
        self._fire_events: list[tuple[int, float]] = []
        in_regime = False
        consec    = 0
        for ts in spread_delta.index:
            sd = spread_delta.get(ts)
            sn = spread.get(ts)
            gd = gspc_delta.get(ts)
            if sd is None or sn is None or gd is None or \
               pd.isna(sd) or pd.isna(sn) or pd.isna(gd):
                consec    = 0
                in_regime = False   # missing data ⇒ leave regime conservatively
                continue
            sd, sn, gd = float(sd), float(sn), float(gd)

            enter_cond = (sd > spread_delta_min) and (gd > 0.0) and (sn > 0.0)
            exit_cond  = sn < exit_spread_max

            if in_regime:
                if exit_cond:
                    in_regime = False
                    consec    = 0
                continue

            # Not yet in regime — count consecutive ENTER days.
            if not enter_cond:
                consec = 0
                continue
            consec += 1
            if consec < persist_days:
                continue

            # Persistence satisfied — record the regime entry.
            d = ts.date() if hasattr(ts, "date") else ts
            if d in date_to_first:
                strength = 1.0 / (1.0 + math.exp(-score_k * (sd - score_x0)))
                self._fire_events.append((date_to_first[d], strength))
            in_regime = True

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
