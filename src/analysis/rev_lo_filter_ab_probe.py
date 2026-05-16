"""rev_lo bearish-body filter A/B probe.

Tests whether the ``close < open`` directional filter in
:class:`~src.signs.rev_peak.RevPeakDetector` actually adds signal, or
whether it just rejects what would otherwise be useful fires.

Two arms (both share the proximity-to-prior-LOW + long-lower-wick
filters; only the directional check differs):

  - **with_filter**  : production rev_lo — fire requires close < open.
  - **without_filter**: same proximity + wick conditions, no body check.

For each fire we compute the **forward 10-bar return** of the stock
(entry at next bar open per the two-bar fill rule, exit at close
+10 bars).  rev_lo predicts an UP bounce, so positive forward return
is the "correct" direction.  Bootstrap CI on the difference of
(direction rate, mean forward return) between arms.

Pre-registered decision (locked before the data is touched):

  - **KEEP filter** (current behaviour) if:
    direction rate drops by > 1.5pp with-→-without (CI lower bound
    below −1.5pp), OR n grows by < 1.2× (filter wasn't rejecting much).
  - **DROP filter** if:
    direction rate change ≥ −1.5pp (lower bound ≥ −1.5pp) AND
    n increases by ≥ 1.5× (filter was actively excluding candidates).
  - **AMBIGUOUS** otherwise → keep filter (conservative default).

Run:
    uv run --env-file devenv python -m src.analysis.rev_lo_filter_ab_probe
"""

from __future__ import annotations

import bisect
import datetime
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger

from src.analysis.exit_benchmark import _load_rep_codes
from src.data.db import get_session
from src.indicators.zigzag import detect_peaks
from src.signs.rev_peak import RevPeakDetector
from src.simulator.cache import DataCache

HORIZON = 10
N_BOOT  = 10_000
SEED    = 20260516

tz = datetime.timezone.utc
END_DT      = datetime.datetime(2026, 5, 15, 23, 59, 59, tzinfo=tz)
BUILD_START = datetime.datetime(2023, 3, 1, tzinfo=tz)

COHORTS = [
    (datetime.date(2024, 4, 1), datetime.date(2025, 3, 31), "FY2024"),
    (datetime.date(2025, 4, 1), datetime.date(2026, 3, 31), "FY2025"),
    (datetime.date(2026, 1, 1), datetime.date(2026, 5, 15), "2026YTD"),
    (datetime.date(2023, 4, 1), datetime.date(2026, 5, 15), "ALL"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Detector variants
# ─────────────────────────────────────────────────────────────────────────────

class RevLoNoFilter(RevPeakDetector):
    """rev_lo detector without the bearish-body directional filter.

    Mirrors :meth:`RevPeakDetector._scan` exactly except the
    ``bar.close >= bar.open`` check is dropped.  Everything else
    (proximity to prior LOW, long-lower-wick) is identical.
    """

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

            # NO directional filter

            bar_range = bar.high - bar.low
            if bar_range <= 0:
                continue
            body_bottom = min(bar.open, bar.close)
            wick = body_bottom - bar.low
            if wick / bar_range < self._wick_min:
                continue

            recent = known[-self._n_peaks:]
            test_price = bar.low
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


# ─────────────────────────────────────────────────────────────────────────────
# Forward-return helper
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Fire:
    stock_code: str
    fire_date:  datetime.date
    fwd_ret:    float


def _bars_by_date(cache: DataCache) -> tuple[dict, list[datetime.date]]:
    bymd: dict[datetime.date, list] = defaultdict(list)
    for b in cache.bars:
        bymd[b.dt.date()].append(b)
    return bymd, sorted(bymd)


def _fwd_return(
    bymd: dict, sorted_dates: list[datetime.date],
    fire_date: datetime.date, h: int = HORIZON,
) -> float | None:
    try:
        i = sorted_dates.index(fire_date)
    except ValueError:
        return None
    if i + h + 1 >= len(sorted_dates):
        return None
    entry = bymd[sorted_dates[i + 1]][0].open
    exit_ = bymd[sorted_dates[i + 1 + h]][-1].close
    if entry is None or exit_ is None or entry <= 0:
        return None
    return (float(exit_) - float(entry)) / float(entry)


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────────────────────────────────────

def _bootstrap_diff(a: list[float], b: list[float],
                    stat_fn, n_boot: int = N_BOOT, seed: int = SEED,
                    ) -> tuple[float, float, float]:
    """Return (point, ci_lo_95, ci_hi_95) for stat_fn(b) − stat_fn(a)."""
    if len(a) < 2 or len(b) < 2:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    na, nb = len(a), len(b)
    diffs: list[float] = []
    for _ in range(n_boot):
        sa = [a[rng.randrange(na)] for _ in range(na)]
        sb = [b[rng.randrange(nb)] for _ in range(nb)]
        try:
            diffs.append(stat_fn(sb) - stat_fn(sa))
        except (statistics.StatisticsError, ZeroDivisionError):
            continue
    if not diffs:
        return float("nan"), float("nan"), float("nan")
    diffs.sort()
    point = stat_fn(b) - stat_fn(a)
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[int(0.975 * len(diffs))]
    return point, lo, hi


def _direction_rate(rets: list[float]) -> float:
    return sum(1 for r in rets if r > 0) / len(rets) if rets else float("nan")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def _filter_to_cohort(fires: list[Fire],
                      c_start: datetime.date, c_end: datetime.date) -> list[Fire]:
    return [f for f in fires if c_start <= f.fire_date <= c_end]


def main() -> None:
    rep_codes = _load_rep_codes("classified2024")
    logger.info("Universe: {} stocks", len(rep_codes))

    fires_with:    list[Fire] = []
    fires_without: list[Fire] = []

    with get_session() as s:
        for code in rep_codes:
            cache = DataCache(code, "1d")
            try:
                cache.load(s, BUILD_START, END_DT)
            except Exception:
                continue
            if not cache.bars:
                continue
            bymd, sdates = _bars_by_date(cache)

            d_with    = RevPeakDetector(cache, side="lo")
            d_without = RevLoNoFilter(cache, side="lo")

            for fi, _score in d_with._fire_events:
                fire_date = cache.bars[fi].dt.date()
                fr = _fwd_return(bymd, sdates, fire_date)
                if fr is not None:
                    fires_with.append(Fire(code, fire_date, fr))
            for fi, _score in d_without._fire_events:
                fire_date = cache.bars[fi].dt.date()
                fr = _fwd_return(bymd, sdates, fire_date)
                if fr is not None:
                    fires_without.append(Fire(code, fire_date, fr))

    print("\n" + "="*92)
    print("REV_LO BEARISH-BODY FILTER A/B — forward 10-bar return per fire")
    print("="*92)

    def _row(label: str, c_start, c_end):
        a = _filter_to_cohort(fires_with,    c_start, c_end)
        b = _filter_to_cohort(fires_without, c_start, c_end)
        ar = [f.fwd_ret for f in a]
        br = [f.fwd_ret for f in b]
        if not ar or not br:
            print(f"  {label:<10} (insufficient n)")
            return None
        n_a, n_b = len(ar), len(br)
        dr_a = _direction_rate(ar) * 100
        dr_b = _direction_rate(br) * 100
        mean_a = statistics.mean(ar) * 100
        mean_b = statistics.mean(br) * 100
        # Bootstrap CI on direction-rate diff (b - a) and on mean diff
        dr_pt, dr_lo, dr_hi = _bootstrap_diff(
            ar, br, lambda xs: sum(1 for x in xs if x > 0) / len(xs) * 100,
        )
        mn_pt, mn_lo, mn_hi = _bootstrap_diff(
            ar, br, lambda xs: statistics.mean(xs) * 100,
        )
        growth = n_b / n_a if n_a else float("nan")
        print(
            f"  {label:<10}  n_with={n_a:>5}  n_without={n_b:>5}  "
            f"(×{growth:.2f})   "
            f"DR_with={dr_a:>5.1f}%  DR_without={dr_b:>5.1f}%  "
            f"ΔDR={dr_pt:>+5.2f}pp CI[{dr_lo:>+5.2f}, {dr_hi:>+5.2f}]   "
            f"mean_with={mean_a:>+5.2f}%  mean_without={mean_b:>+5.2f}%  "
            f"Δmean={mn_pt:>+5.2f}pp CI[{mn_lo:>+5.2f}, {mn_hi:>+5.2f}]"
        )
        return dict(n_a=n_a, n_b=n_b, dr_pt=dr_pt, dr_lo=dr_lo, dr_hi=dr_hi,
                    mn_pt=mn_pt, mn_lo=mn_lo, mn_hi=mn_hi, growth=growth)

    print()
    rows = {}
    for c_start, c_end, lbl in COHORTS:
        rows[lbl] = _row(lbl, c_start, c_end)

    print("\n" + "="*92)
    print("PRE-REGISTERED DECISION (per ALL cohort)")
    print("="*92)
    agg = rows.get("ALL")
    if not agg:
        print("  Insufficient aggregate data.")
        return

    dr_drop_ok    = (not math.isnan(agg["dr_lo"])) and agg["dr_lo"] >= -1.5
    growth_ok     = agg["growth"] >= 1.5
    minimal_diff  = (not math.isnan(agg["growth"])) and agg["growth"] < 1.2
    dr_drop_real  = (not math.isnan(agg["dr_hi"])) and agg["dr_hi"] < -1.5

    print(f"  Direction-rate Δ(without − with) CI: [{agg['dr_lo']:+.2f}, {agg['dr_hi']:+.2f}]")
    print(f"  n growth (×): {agg['growth']:.2f}")
    print(f"  CI lower ≥ -1.5pp (no material DR loss)?  {'✓' if dr_drop_ok else '✗'}")
    print(f"  n grew ≥ 1.5×?                            {'✓' if growth_ok else '✗'}")
    print()
    if dr_drop_real:
        print("  → KEEP FILTER: direction rate drops materially when filter is removed.")
    elif minimal_diff:
        print("  → KEEP FILTER (no-op): filter rejects little; removing changes nothing.")
    elif dr_drop_ok and growth_ok:
        print("  → DROP FILTER: rev_lo gets ≥1.5× more fires with no material DR loss.")
    else:
        print("  → AMBIGUOUS: keep filter (conservative default).")
    print("="*92 + "\n")


if __name__ == "__main__":
    main()
