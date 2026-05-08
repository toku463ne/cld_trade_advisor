"""corr_flip — Correlation Regime Flip sign detector.

Fires on a bar where rolling corr(stock, indicator) crosses from negative to
positive, having been negative for at least ``min_neg_bars`` consecutive bars.

Score = neg_depth × 0.4 + neg_duration_norm × 0.3 + cross_strength × 0.3
  neg_depth         = min(|min corr during negative phase|, 1.0)
  neg_duration_norm = min(consecutive_neg_bars / 20, 1.0)
  cross_strength    = min(corr_at_crossing / 0.5, 1.0)

Longer/deeper negative phases followed by a strong upward crossing score
higher — these represent a more decisive re-coupling after a divergence.

Valid for up to ``valid_bars`` bars after firing, provided the rolling corr
at the query bar is still > 0 (re-coupling holding).
"""
# ── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
# uv run --env-file devenv python -m src.analysis.sign_benchmark \
#     --sign corr_flip --cluster-set classified2023 \
#     --start 2023-04-01 --end 2025-03-31 --gran 1d
# run_id=23  n=232  direction_rate=56.5%  p≈0.048
# bench_flw=0.057  bench_rev=0.027  mean_bars=12.7  (mag_flw=0.101  mag_rev=0.062)
# → PROVISIONAL (FLW) — borderline p and small n; best bench_flw of all signs
# Low-corr only (run_id=46, --corr-mode low):
#   uv run --env-file devenv python -m src.analysis.sign_benchmark \
#       --sign corr_flip --cluster-set classified2023 \
#       --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
#   n=215  direction_rate=56.2%  p≈0.069  bench_flw=0.056
#   → Note: mode-neutral; sign captures re-coupling after divergence regardless of typical corr level

from __future__ import annotations

import bisect
import datetime
from dataclasses import dataclass

import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache


@dataclass
class _FireEvent:
    bar_index: int
    score: float
    neg_bars: int


class CorrFlipDetector:
    """Initialise once per (stock, indicator) cache pair; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        indicator_cache: DataCache,
        window: int       = 20,
        min_neg_bars: int = 5,
    ) -> None:
        self._stock_code   = stock_cache.stock_code
        self._dts          = [b.dt for b in stock_cache.bars]
        self._min_neg_bars = min_neg_bars

        stock_s = pd.Series({b.dt: b.close for b in stock_cache.bars})
        ind_s   = pd.Series({b.dt: b.close for b in indicator_cache.bars})

        aligned = pd.concat(
            [stock_s.pct_change().rename("stock"), ind_s.pct_change().rename("ind")],
            axis=1,
        )
        self._corr: pd.Series = (
            aligned["stock"]
            .rolling(window, min_periods=max(5, window // 2))
            .corr(aligned["ind"])
        )
        self._fire_events: list[_FireEvent] = self._scan()

    def _scan(self) -> list[_FireEvent]:
        events: list[_FireEvent] = []
        consecutive_neg   = 0
        min_corr_in_phase = 0.0

        for i, dt in enumerate(self._dts):
            cr_raw = self._corr.get(dt)
            if cr_raw is None or pd.isna(float(cr_raw)):
                consecutive_neg   = 0
                min_corr_in_phase = 0.0
                continue
            cr = float(cr_raw)

            if cr < 0:
                if consecutive_neg == 0:
                    min_corr_in_phase = cr
                else:
                    min_corr_in_phase = min(min_corr_in_phase, cr)
                consecutive_neg += 1
            else:
                if consecutive_neg >= self._min_neg_bars:
                    neg_depth         = min(abs(min_corr_in_phase), 1.0)
                    neg_duration_norm = min(consecutive_neg / 20.0, 1.0)
                    cross_strength    = min(cr / 0.5, 1.0)
                    score = neg_depth * 0.4 + neg_duration_norm * 0.3 + cross_strength * 0.3
                    events.append(_FireEvent(i, score, consecutive_neg))
                consecutive_neg   = 0
                min_corr_in_phase = 0.0

        return events

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 5,
    ) -> SignResult | None:
        """Return the most recent valid corr_flip sign at *as_of*, or None."""
        idx = bisect.bisect_right(self._dts, as_of) - 1
        if idx < 0 or not self._fire_events:
            return None

        fire_ev: _FireEvent | None = None
        for ev in reversed(self._fire_events):
            if ev.bar_index > idx:
                continue
            if idx - ev.bar_index > valid_bars:
                break
            fire_ev = ev
            break

        if fire_ev is None:
            return None

        corr_now = self._corr.get(self._dts[idx])
        if corr_now is None or pd.isna(float(corr_now)) or float(corr_now) <= 0:
            return None

        valid_until_idx = min(fire_ev.bar_index + valid_bars, len(self._dts) - 1)
        return SignResult(
            sign_type="corr_flip",
            stock_code=self._stock_code,
            score=fire_ev.score,
            fired_at=self._dts[fire_ev.bar_index],
            valid_until=self._dts[valid_until_idx],
        )
