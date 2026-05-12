"""brk_bol — Bollinger Band Breakout sign detector.

Fires on the bar where close *decisively* breaks above the upper Bollinger Band
(N-bar SMA + n_std × rolling σ), i.e.:
  - The previous ``min_below_bars`` bars all closed at-or-below the upper band
    (volatility containment phase), AND
  - The current bar closes above the upper band, AND
  - The current bar's volume is at least ``volume_mult`` × the rolling-mean
    volume over the BB window (default 1.5×). Without volume confirmation, a
    "breakout" can simply be a quiet drift while σ stays small (the band moves
    toward price, rather than price expanding through the band).

A pure 1-bar crossover is rejected — we require sustained pre-crossover
containment AND volume confirmation to filter noisy oscillation around the band.

Score = min(0.5 + excess × 0.5, 1.0)
  excess = (close − upper_band) / σ   [how many σ above the upper band]
  A close right at the upper band scores 0.5; each additional σ adds 0.5 more.

Valid for up to ``valid_bars`` bars after firing, provided close remains > upper band.
If price retreats below the upper band the sign expires early.
"""
# ── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
# uv run --env-file devenv python -m src.analysis.sign_benchmark \
#     --sign brk_bol --cluster-set classified2023 \
#     --start 2023-04-01 --end 2025-03-31 --gran 1d
# run_id=27  n=2540  direction_rate=52.0%  p≈0.044
# bench_flw=0.047  bench_rev=0.034  mean_bars=12.5  (mag_flw=0.090  mag_rev=0.071)
# → SKIP (downgraded from PROVISIONAL after sign_validate)
#   Permutation test: emp_p=0.028 (passes)
#   Dedup check:  dedup n=2189 (×1.1)  dedup DR=51.7%  dedup p=0.109 — loses significance
#   Regime split: bear DR=54.0% (p=0.027)  bull DR=50.6% (p=0.630)
#   → 2/3 of events are in bull regime where DR is 50.6% (random). The headline p=0.044
#     was entirely driven by bear-regime events. Add bear-regime gate + volume filter before reuse.
# Low-corr only (run_id=42, --corr-mode low):
#   uv run --env-file devenv python -m src.analysis.sign_benchmark \
#       --sign brk_bol --cluster-set classified2023 \
#       --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
#   n=636  direction_rate=51.4%  p≈0.48  bench_flw=0.050
#   → Note: loses significance on low-corr stocks; use on all corr regimes

from __future__ import annotations

import bisect
import datetime

import pandas as pd

from src.signs.base import SignResult
from src.simulator.cache import DataCache

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["brk_bol"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "brk_bol": (
        "**Bollinger Band Breakout** — "
        "price closes above the upper Bollinger Band. "
        "Indicates volatility expansion in the upward direction."
    ),
}


class BrkBolDetector:
    """Initialise once per stock hourly cache; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        window: int  = 20,
        n_std: float = 2.0,
        min_below_bars: int = 5,
        volume_mult: float = 1.5,
    ) -> None:
        self._stock_code     = stock_cache.stock_code
        self._dts            = [b.dt for b in stock_cache.bars]
        self._min_below_bars = min_below_bars
        self._volume_mult    = volume_mult

        close_s   = pd.Series({b.dt: b.close for b in stock_cache.bars})
        min_p     = max(5, window // 2)
        mid       = close_s.rolling(window, min_periods=min_p).mean()
        std       = close_s.rolling(window, min_periods=min_p).std()
        self._upper  = mid + n_std * std
        self._std    = std
        self._close  = close_s

        vol_s         = pd.Series({b.dt: b.volume for b in stock_cache.bars}, dtype=float)
        self._volume  = vol_s
        self._vol_avg = vol_s.rolling(window, min_periods=min_p).mean()

        self._fire_events: list[tuple[int, float]] = self._scan()

    def _scan(self) -> list[tuple[int, float]]:
        events: list[tuple[int, float]] = []
        dts = self._dts
        N = self._min_below_bars
        for i in range(N, len(dts)):
            # Current bar must close above the upper band.
            dt  = dts[i]
            c   = self._close.get(dt)
            u   = self._upper.get(dt)
            sig = self._std.get(dt)
            if c is None or u is None or sig is None:
                continue
            c, u, sig = float(c), float(u), float(sig)
            if pd.isna(c) or pd.isna(u) or pd.isna(sig) or sig == 0 or c <= u:
                continue
            # Volume confirmation: today's volume ≥ mult × rolling-mean volume.
            v   = self._volume.get(dt)
            v_a = self._vol_avg.get(dt)
            if v is None or v_a is None:
                continue
            v, v_a = float(v), float(v_a)
            if pd.isna(v) or pd.isna(v_a) or v_a <= 0 or v < self._volume_mult * v_a:
                continue
            # Prior N bars must all close at-or-below the upper band.
            contained = True
            for k in range(1, N + 1):
                pj = self._close.get(dts[i - k])
                uj = self._upper.get(dts[i - k])
                if pj is None or uj is None:
                    contained = False
                    break
                pj, uj = float(pj), float(uj)
                if pd.isna(pj) or pd.isna(uj) or pj > uj:
                    contained = False
                    break
            if not contained:
                continue
            excess = (c - u) / sig
            score  = min(0.5 + excess * 0.5, 1.0)
            events.append((i, score))
        return events

    def detect(
        self,
        as_of: datetime.datetime,
        valid_bars: int = 3,
    ) -> SignResult | None:
        idx = bisect.bisect_right(self._dts, as_of) - 1
        if idx < 0 or not self._fire_events:
            return None

        for fi, score in reversed(self._fire_events):
            if fi > idx:
                continue
            if idx - fi > valid_bars:
                break
            # Situational: price still above upper band
            dt_now = self._dts[idx]
            c_now  = self._close.get(dt_now)
            u_now  = self._upper.get(dt_now)
            if c_now is None or u_now is None:
                return None
            if pd.isna(float(c_now)) or pd.isna(float(u_now)) or float(c_now) <= float(u_now):
                return None

            valid_until_idx = min(fi + valid_bars, len(self._dts) - 1)
            return SignResult(
                sign_type="brk_bol",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
