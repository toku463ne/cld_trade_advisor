"""rev_peak — Price Near Recent Same-Side Zigzag Peak (Reversal).

Fires on the bar when the bar's tested price is within ``proximity_pct``
of one of the last ``n_peaks`` confirmed same-type zigzag peaks of the
input cache (typically daily bars).

  side='lo'  → test_price = bar.low  near a prior confirmed LOW
               sign_type = "rev_lo"  — expect UP bounce (support test)
  side='hi'  → test_price = bar.high near a prior confirmed HIGH
               sign_type = "rev_hi"  — expect DOWN reversal (resistance test)

Only peaks whose zigzag confirmation has fully passed before the current
bar are used — no look-ahead. Two filters are applied at firing time:

  Directional approach
    The bar must be moving toward the level: close < open for rev_lo;
    close > open for rev_hi.

  Long rejection wick (hammer / shooting-star body)
    For rev_lo, the lower wick — the distance from min(open, close) to low —
    must be at least ``wick_min`` × (high − low). This captures the
    buyer-stepped-in intraday rejection that distinguishes a real reversal
    from a straight slide through support. For rev_hi the upper wick —
    high − max(open, close) — is required.

Score = 1 − proximity / proximity_pct
  1.0 when price is exactly at the prior peak; 0.0 at the boundary.

Valid for up to ``valid_bars`` bars after firing (time-bounded only).
"""
# ── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
# rev_lo (side='lo'):
#   uv run --env-file devenv python -m src.analysis.sign_benchmark \
#       --sign rev_lo --cluster-set classified2023 \
#       --start 2023-04-01 --end 2025-03-31 --gran 1d
#   run_id=28  n=1829  direction_rate=58.6%  p<0.001
#   bench_flw=0.049  bench_rev=0.028  mean_bars=13.0  (mag_flw=0.083  mag_rev=0.067)
#   → RECOMMEND (FLW) — strong and significant; best direction_rate among high-n signs
# rev_lo low-corr only (run_id=43, --corr-mode low):
#   uv run --env-file devenv python -m src.analysis.sign_benchmark \
#       --sign rev_lo --cluster-set classified2023 \
#       --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
#   n=356  direction_rate=57.9%  p≈0.003  bench_flw=0.043
#   → Note: corr-neutral; support-test thesis holds regardless of index coupling
# rev_hi (side='hi'):
#   uv run --env-file devenv python -m src.analysis.sign_benchmark \
#       --sign rev_hi --cluster-set classified2023 \
#       --start 2023-04-01 --end 2025-03-31 --gran 1d
#   run_id=29  n=2180  direction_rate=50.5%  p≈0.64
#   bench_flw=0.039  bench_rev=0.034  mean_bars=12.4  (mag_flw=0.077  mag_rev=0.069)
#   → SKIP — no directional edge at prior-high resistance
# rev_hi low-corr only (run_id=44, --corr-mode low):
#   uv run --env-file devenv python -m src.analysis.sign_benchmark \
#       --sign rev_hi --cluster-set classified2023 \
#       --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
#   n=520  direction_rate=53.8%  p≈0.083  bench_flw=0.042
#   → Note: slight improvement on low-corr stocks but still borderline; remains SKIP

from __future__ import annotations

import bisect
import datetime

from src.indicators.zigzag import detect_peaks
from src.signs.base import SignResult
from src.simulator.cache import DataCache

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["rev_lo", "rev_hi"]
SIGN_DESCRIPTIONS: dict[str, str] = {
    "rev_lo": (
        "**N-Day Low Reversal** — "
        "contrarian: stock touches a recent multi-day low; mean-reversion bounce expected."
    ),
    "rev_hi": (
        "**N-Day High Breakout** — "
        "stock touches a recent multi-day high; momentum continuation expected."
    ),
}


class RevPeakDetector:
    """Initialise once per stock cache; call detect() per bar."""

    _ZZ_SIZE = 5
    _ZZ_MID  = 2

    def __init__(
        self,
        stock_cache: DataCache,
        proximity_pct: float = 0.005,
        side: str            = "lo",
        n_peaks: int         = 2,
        wick_min: float      = 0.4,
    ) -> None:
        assert side in ("lo", "hi"), "side must be 'lo' or 'hi'"
        self._stock_code = stock_cache.stock_code
        self._side       = side
        self._proximity  = proximity_pct
        self._wick_min   = wick_min
        bars             = stock_cache.bars
        self._dts        = [b.dt for b in bars]
        self._sign_type  = "rev_lo" if side == "lo" else "rev_hi"

        target_dir = -2 if side == "lo" else 2

        # ── Zigzag peaks (single pass over the cache; no look-ahead — each peak
        #    becomes observable only after _ZZ_SIZE bars of confirmation). ──
        highs = [b.high for b in bars]
        lows  = [b.low  for b in bars]
        peaks = detect_peaks(highs, lows, size=self._ZZ_SIZE, middle_size=self._ZZ_MID)

        # obs_peaks: (observable_from_bar_idx, formation_bar_idx, price)
        obs_peaks: list[tuple[int, int, float]] = []
        for p in peaks:
            if p.direction != target_dir:
                continue
            obs_from = p.bar_index + self._ZZ_SIZE
            if obs_from >= len(bars):
                continue
            obs_peaks.append((obs_from, p.bar_index, p.price))

        # Sort by (observable_from) for efficient scanning.
        obs_peaks.sort(key=lambda x: x[0])
        self._obs_peaks = obs_peaks
        self._n_peaks   = n_peaks

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

            # Long rejection wick: ≥ wick_min × range on the test side.
            bar_range = bar.high - bar.low
            if bar_range <= 0:
                continue
            if self._side == "lo":
                body_bottom = min(bar.open, bar.close)
                wick = body_bottom - bar.low
            else:
                body_top    = max(bar.open, bar.close)
                wick = bar.high - body_top
            if wick / bar_range < self._wick_min:
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
