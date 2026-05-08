"""rev_nday — Price Reaches N-Day High/Low (Reversal).

Fires on the first hourly bar of a session where the bar's extreme price
reaches or exceeds the reference N-day high (side='hi') or falls to/below
the N-day low (side='lo'), computed from the *prior* N complete trading
days — no look-ahead.

  side='hi'  → bar.high >= N-day reference high → sign_type = "rev_nhi"
               Expect DOWN reversal (exhaustion at multi-day high)
  side='lo'  → bar.low  <= N-day reference low  → sign_type = "rev_nlo"
               Expect UP  bounce   (exhaustion at multi-day low)

Directional filter: for rev_nhi the bar must close below its open (bearish
body confirms rejection); for rev_nlo the bar must close above its open
(bullish body confirms rejection of the low).

Score = 1.0 (uniform — the level touch is the signal; strength is captured
by the n_days parameter choice).

Valid for up to ``valid_bars`` bars after firing (time-bounded only).
"""
# ── Benchmark (classified2023 · 164 stocks · 2023-04-01–2025-03-31 · gran=1d) ──
# rev_nhi: run_id=30  n=3579  direction_rate=54.0%  p<0.001
#   bench_flw=0.047  bench_rev=0.033  mean_bars=12.6
#   Regime split: bear DR=51.2% (p=0.47)  bull DR=54.4% (p<0.001)
#   → 2-year result was RECOMMEND but driven by bull market (FY2023+FY2024).
#
# ── 7-year cross-validation (FY2018–FY2024) ──
# rev_nhi: pooled DR=48.9%  p≈0.024  perm_pass=2/7
# → PROVISIONAL (bull-only): no edge in bear regime across all FYs. Only use when
#   N225 last confirmed zigzag peak is a LOW. In bear/neutral regimes treat as SKIP.
# rev_nlo (side='lo') is handled by RevNloDetector in rev_nlo.py — see that file.

from __future__ import annotations

import bisect
import datetime

from src.signs.base import SignResult
from src.simulator.cache import DataCache


class RevNDayDetector:
    """Initialise once per stock hourly cache; call detect() per bar."""

    def __init__(
        self,
        stock_cache: DataCache,
        n_days: int = 20,
        side: str   = "hi",
    ) -> None:
        assert side in ("hi", "lo"), "side must be 'hi' or 'lo'"
        self._stock_code = stock_cache.stock_code
        self._side       = side
        bars             = stock_cache.bars
        self._dts        = [b.dt for b in bars]
        self._sign_type  = "rev_nhi" if side == "hi" else "rev_nlo"

        # ── Build daily high/low ─────────────────────────────────────────────
        daily_highs: dict[datetime.date, float] = {}
        daily_lows:  dict[datetime.date, float] = {}
        for b in bars:
            d = b.dt.date()
            if d not in daily_highs or b.high > daily_highs[d]:
                daily_highs[d] = b.high
            if d not in daily_lows or b.low < daily_lows[d]:
                daily_lows[d] = b.low

        trading_dates = sorted(daily_highs)

        # ── Reference level: max/min of the prior N complete trading days ────
        ref_level: dict[datetime.date, float] = {}
        for i, d in enumerate(trading_dates):
            if i < n_days:
                continue
            prior = trading_dates[i - n_days : i]
            if side == "hi":
                ref_level[d] = max(daily_highs[pd] for pd in prior)
            else:
                ref_level[d] = min(daily_lows[pd] for pd in prior)

        # ── Group hourly bars by trading date ────────────────────────────────
        date_to_bars: dict[datetime.date, list[tuple[int, object]]] = {}
        for i, b in enumerate(bars):
            date_to_bars.setdefault(b.dt.date(), []).append((i, b))

        # ── Scan: at most one fire per trading day ───────────────────────────
        self._fire_events: list[tuple[int, float]] = []
        for d in sorted(date_to_bars):
            ref = ref_level.get(d)
            if ref is None:
                continue
            for bar_idx, bar in date_to_bars[d]:
                if side == "hi":
                    if bar.high < ref:
                        continue
                    if bar.close >= bar.open:   # must be bearish bar
                        continue
                else:
                    if bar.low > ref:
                        continue
                    if bar.close <= bar.open:   # must be bullish bar
                        continue
                self._fire_events.append((bar_idx, 1.0))
                break   # fire at most once per day

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
