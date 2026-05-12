"""div_bar — N225 Divergence sign detector.

Fires on a single 1h bar when all hold:
  - N225 bar return < -1.5 %
  - Stock bar return > +0.3 %
  - Rolling 20-bar corr(stock, N225) > +0.30

Score = (stock_ret - n225_ret) × corr_at_fire
  Higher prior coupling and wider return gap both increase the score.

Valid for up to ``valid_bars`` bars after firing, provided the rolling
corr(stock, N225) at the query bar is still < 0 (divergence phase active).
If corr returns to positive the sign expires early.
"""
# ── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
# uv run --env-file devenv python -m src.analysis.sign_benchmark \
#     --sign div_bar --cluster-set classified2023 \
#     --start 2023-04-01 --end 2025-03-31 --gran 1d
# run_id=20  n=17  direction_rate=35.3%  p≈0.23
# bench_flw=0.036  bench_rev=0.046  mean_bars=14.2  (mag_flw=0.102  mag_rev=0.071)
# → SKIP (n too small for significance; designed for 1h intraday bars, not daily)

from __future__ import annotations

import bisect
import datetime

import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

SIGN_VALID: bool = False  # fires intrabar; not suitable for daily regime_sign benchmark
SIGN_NAMES: list[str] = ["div_bar"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "div_bar": (
        "**Bar Divergence** — "
        "stock rises (>0.3%) on a bar where N225 falls (>1.5%) and prior correlation is positive. "
        "Independent buying absorbs the broad market fall."
    ),
}


class DivBarDetector:
    """Initialise once per (stock, N225) cache pair; call detect() per bar."""

    _N225_RET_MAX  = -0.015
    _STOCK_RET_MIN =  0.003
    _CORR_MIN      =  0.30

    def __init__(
        self,
        stock_cache: DataCache,
        n225_cache: DataCache,
        window: int = 20,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        self._dts        = [b.dt for b in stock_cache.bars]

        stock_s = pd.Series({b.dt: b.close for b in stock_cache.bars})
        n225_s  = pd.Series({b.dt: b.close for b in n225_cache.bars})

        stock_ret = stock_s.pct_change()
        n225_ret  = n225_s.pct_change()

        aligned = pd.concat(
            [stock_ret.rename("stock"), n225_ret.rename("n225")],
            axis=1,
        )
        self._corr: pd.Series = (
            aligned["stock"]
            .rolling(window, min_periods=max(5, window // 2))
            .corr(aligned["n225"])
        )
        # Condition uses the PREVIOUS bar's corr — the state before the divergence
        # bar itself (which would drag the rolling corr negative immediately).
        self._corr_prev: pd.Series = self._corr.shift(1)
        self._stock_ret = stock_ret
        self._n225_ret  = n225_ret

        self._fire_events: list[tuple[int, float]] = self._scan()

    def _scan(self) -> list[tuple[int, float]]:
        events: list[tuple[int, float]] = []
        for i, dt in enumerate(self._dts):
            sr = self._stock_ret.get(dt)
            nr = self._n225_ret.get(dt)
            cr = self._corr_prev.get(dt)  # prior coupling, before this bar
            if sr is None or nr is None or cr is None:
                continue
            sr, nr, cr = float(sr), float(nr), float(cr)
            if pd.isna(sr) or pd.isna(nr) or pd.isna(cr):
                continue
            if nr < self._N225_RET_MAX and sr > self._STOCK_RET_MIN and cr > self._CORR_MIN:
                events.append((i, (sr - nr) * cr))
        return events

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 5,
    ) -> SignResult | None:
        """Return the most recent valid div_bar sign at *as_of*, or None."""
        idx = bisect.bisect_right(self._dts, as_of) - 1
        if idx < 0 or not self._fire_events:
            return None

        fire_idx: int | None = None
        fire_score: float    = 0.0
        for fi, score in reversed(self._fire_events):
            if fi > idx:
                continue
            if idx - fi > valid_bars:
                break
            fire_idx, fire_score = fi, score
            break

        if fire_idx is None:
            return None

        corr_now = self._corr.get(self._dts[idx])
        if corr_now is None or pd.isna(float(corr_now)) or float(corr_now) >= 0:
            return None

        valid_until_idx = min(fire_idx + valid_bars, len(self._dts) - 1)
        return SignResult(
            sign_type="div_bar",
            stock_code=self._stock_code,
            score=fire_score,
            fired_at=self._dts[fire_idx],
            valid_until=self._dts[valid_until_idx],
        )
