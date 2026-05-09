"""div_gap — Opening Gap Divergence sign detector.

Fires on the first hourly bar of each trading session when:
  - Stock open > previous session close by > +STOCK_GAP_MIN  (gap up)
  - N225 open < previous session close by < -N225_GAP_MAX    (gap down)

Score = min(stock_gap / 0.02, 1.0) × min(|n225_gap| / 0.02, 1.0)
  Larger gaps in both directions produce a higher score.

Valid for up to ``valid_bars`` bars after firing (time-bounded only).
Overnight buyers are already committed; no additional situational check.
"""
# ── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
# uv run --env-file devenv python -m src.analysis.sign_benchmark \
#     --sign div_gap --cluster-set classified2023 \
#     --start 2023-04-01 --end 2025-03-31 --gran 1d
# run_id=22  n=1037  direction_rate=58.2%  p<0.001
# bench_flw=0.051  bench_rev=0.029  mean_bars=12.7  (mag_flw=0.087  mag_rev=0.070)
# → RECOMMEND (FLW) — highly significant; highest bench_flw among all signs
# Permutation & regime split (sign_validate):
#   Permutation test: emp_p=<0.001  dedup n=924 (×1.1)  dedup DR=57.7%  stable
#   Regime split: bear DR=62.6% (p<0.001, n=447)  bull DR=54.1% (p=0.062, n=529)
#   → Strongest in bear regime; diverging from a falling index is a more meaningful signal.
# Low-corr only (run_id=39, --corr-mode low):
#   uv run --env-file devenv python -m src.analysis.sign_benchmark \
#       --sign div_gap --cluster-set classified2023 \
#       --start 2023-04-01 --end 2025-03-31 --gran 1d --corr-mode low
#   n=355  direction_rate=54.6%  p≈0.083  bench_flw=0.046
#   → Note: WORSE on low-corr stocks; div_gap works BEST when a high-corr stock diverges from a gapping index

from __future__ import annotations

import bisect
import datetime

from src.signs.base import SignResult
from src.simulator.cache import DataCache

_STOCK_GAP_MIN = 0.005   # stock gap up > +0.5 %
_N225_GAP_MAX  = -0.005  # N225 gap down < -0.5 %
_SCORE_CAP     = 0.02    # gap magnitude at which score saturates to 1.0

SIGN_VALID: bool = True
SIGN_NAMES: list[str] = ["div_gap"]


class DivGapDetector:
    """Initialise once per (stock, N225) hourly cache pair; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        n225_cache: DataCache,
    ) -> None:
        self._stock_code = stock_cache.stock_code
        stock_bars       = stock_cache.bars
        n225_bars        = n225_cache.bars
        self._dts        = [b.dt for b in stock_bars]

        # Previous-session close and session-open per date — N225
        n225_by_date: dict[datetime.date, list] = {}
        for b in n225_bars:
            n225_by_date.setdefault(b.dt.date(), []).append(b)
        n225_dates_sorted = sorted(n225_by_date)
        n225_prev_close: dict[datetime.date, float] = {}
        n225_sess_open:  dict[datetime.date, float] = {}
        for i, d in enumerate(n225_dates_sorted):
            n225_sess_open[d] = n225_by_date[d][0].open
            if i > 0:
                n225_prev_close[d] = n225_by_date[n225_dates_sorted[i - 1]][-1].close

        # Previous-session close per date — stock
        stock_by_date: dict[datetime.date, list] = {}
        for b in stock_bars:
            stock_by_date.setdefault(b.dt.date(), []).append(b)
        stock_dates_sorted = sorted(stock_by_date)
        stock_prev_close: dict[datetime.date, float] = {}
        for i, d in enumerate(stock_dates_sorted):
            if i > 0:
                stock_prev_close[d] = stock_by_date[stock_dates_sorted[i - 1]][-1].close

        # Scan: fire on first bar of each session meeting gap conditions
        self._fire_events: list[tuple[int, float]] = []
        for i, b in enumerate(stock_bars):
            d = b.dt.date()
            if i > 0 and stock_bars[i - 1].dt.date() == d:
                continue  # not first bar of session

            spc = stock_prev_close.get(d)
            npc = n225_prev_close.get(d)
            nso = n225_sess_open.get(d)
            if spc is None or npc is None or nso is None or spc == 0 or npc == 0:
                continue

            stock_gap = b.open / spc - 1.0
            n225_gap  = nso / npc - 1.0

            if stock_gap > _STOCK_GAP_MIN and n225_gap < _N225_GAP_MAX:
                score = (
                    min(stock_gap / _SCORE_CAP, 1.0)
                    * min(abs(n225_gap) / _SCORE_CAP, 1.0)
                )
                self._fire_events.append((i, score))

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
                sign_type="div_gap",
                stock_code=self._stock_code,
                score=score,
                fired_at=self._dts[fi],
                valid_until=self._dts[valid_until_idx],
            )
        return None
