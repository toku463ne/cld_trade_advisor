"""rev_lo bearish-body filter A/B, stratified by breadth regime.

Extension of rev_lo_filter_ab_probe.py — the aggregate A/B returned
AMBIGUOUS (DR essentially unchanged) but n doubled.  This probe asks
whether the doubled n reveals regime-conditional structure that the
aggregate hides: specifically, do bullish-body fires (which the
filter rejects) do better or worse on AND-HIGH days (high
reversal-risk regime, per project_breadth_indicators)?

Per-fire stratification by the (rev_nhi top-quintile ∧ SMA50
top-quintile) AND-gate at the fire date.  For each (arm × cell)
combination we report n, DR, mean forward 10-bar return, and
bootstrap the (without − with) difference within each cell.

Pre-registered AND-HIGH-cell decision (locked before running):

  - **DROP filter (conditional)** if AND-HIGH cell shows
    n_without ≥ 1.5× n_with AND ΔDR CI lower bound ≥ −1pp.
  - **KEEP filter (real work)** if AND-HIGH cell ΔDR CI upper bound
    < −1.5pp.
  - **STILL AMBIGUOUS** otherwise — defer; today's verdict (keep filter
    conservatively) stands.

Run:
    uv run --env-file devenv python -m src.analysis.rev_lo_filter_regime_ab
"""

from __future__ import annotations

import bisect
import datetime
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass

from loguru import logger

from src.analysis.exit_benchmark import _load_rep_codes
from src.data.db import get_session
from src.indicators.rev_n_regime import RevNRegime
from src.indicators.sma_regime import SMARegime
from src.signs.rev_peak import RevPeakDetector
from src.simulator.cache import DataCache

HORIZON = 10
N_BOOT  = 10_000
SEED    = 20260516

tz = datetime.timezone.utc
END_DT      = datetime.datetime(2026, 5, 15, 23, 59, 59, tzinfo=tz)
BUILD_START = datetime.datetime(2023, 3, 1, tzinfo=tz)


# ─────────────────────────────────────────────────────────────────────────────
# Detector variants (mirror rev_lo_filter_ab_probe)
# ─────────────────────────────────────────────────────────────────────────────

class RevLoNoFilter(RevPeakDetector):
    """rev_lo without the bearish-body directional filter."""

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


@dataclass
class Fire:
    stock_code: str
    fire_date:  datetime.date
    fwd_ret:    float
    and_high:   bool
    nhi_high:   bool
    sma_high:   bool


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bars_by_date(cache: DataCache) -> tuple[dict, list[datetime.date]]:
    bymd: dict[datetime.date, list] = defaultdict(list)
    for b in cache.bars:
        bymd[b.dt.date()].append(b)
    return bymd, sorted(bymd)


def _fwd_return(bymd, sorted_dates, fire_date, h: int = HORIZON) -> float | None:
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


def _bootstrap_diff(a, b, stat_fn, n_boot=N_BOOT, seed=SEED):
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


def _dr_pct(rets: list[float]) -> float:
    return sum(1 for r in rets if r > 0) / len(rets) * 100 if rets else float("nan")


def _mean_pct(rets: list[float]) -> float:
    return statistics.mean(rets) * 100 if rets else float("nan")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    rep_codes = _load_rep_codes("classified2024")
    logger.info("Universe: {} stocks", len(rep_codes))

    # Load all caches
    caches: dict[str, DataCache] = {}
    with get_session() as s:
        for code in rep_codes:
            cache = DataCache(code, "1d")
            try:
                cache.load(s, BUILD_START, END_DT)
            except Exception:
                continue
            if cache.bars:
                caches[code] = cache

    # Build regime indicators (need universe-wide breadth)
    all_dates = sorted({b.dt.date() for c in caches.values() for b in c.bars})
    logger.info("Building RevNRegime + SMARegime over {} dates …", len(all_dates))
    rev_regime = RevNRegime.build(caches, all_dates)
    sma_regime = SMARegime.build(caches, all_dates)

    # Per-date regime state (precompute)
    is_nhi_high = {d: rev_regime.is_high(d) for d in all_dates}
    is_sma_high = {d: sma_regime.is_high(d) for d in all_dates}
    is_and_high = {d: (is_nhi_high[d] and is_sma_high[d]) for d in all_dates}

    fires_with:    list[Fire] = []
    fires_without: list[Fire] = []

    for code, cache in caches.items():
        bymd, sdates = _bars_by_date(cache)
        d_with    = RevPeakDetector(cache, side="lo")
        d_without = RevLoNoFilter(cache, side="lo")

        for fi, _score in d_with._fire_events:
            fire_date = cache.bars[fi].dt.date()
            fr = _fwd_return(bymd, sdates, fire_date)
            if fr is None: continue
            fires_with.append(Fire(
                code, fire_date, fr,
                and_high=is_and_high.get(fire_date, False),
                nhi_high=is_nhi_high.get(fire_date, False),
                sma_high=is_sma_high.get(fire_date, False),
            ))
        for fi, _score in d_without._fire_events:
            fire_date = cache.bars[fi].dt.date()
            fr = _fwd_return(bymd, sdates, fire_date)
            if fr is None: continue
            fires_without.append(Fire(
                code, fire_date, fr,
                and_high=is_and_high.get(fire_date, False),
                nhi_high=is_nhi_high.get(fire_date, False),
                sma_high=is_sma_high.get(fire_date, False),
            ))

    print("\n" + "="*100)
    print("REV_LO BEARISH-BODY FILTER × REGIME STRATIFICATION")
    print(f"  Cells stratified by AND-gate (rev_nhi top-Q ∧ SMA50 top-Q) at fire date")
    print("="*100)

    def _cell(label, sel_with, sel_without):
        ar = [f.fwd_ret for f in fires_with    if sel_with(f)]
        br = [f.fwd_ret for f in fires_without if sel_without(f)]
        n_a, n_b = len(ar), len(br)
        if n_a < 2 or n_b < 2:
            print(f"  {label:<26}  n_with={n_a:>4}  n_without={n_b:>4}  (insufficient n)")
            return None
        dr_a, dr_b = _dr_pct(ar), _dr_pct(br)
        mn_a, mn_b = _mean_pct(ar), _mean_pct(br)
        dr_pt, dr_lo, dr_hi = _bootstrap_diff(ar, br, _dr_pct)
        mn_pt, mn_lo, mn_hi = _bootstrap_diff(ar, br, _mean_pct)
        growth = n_b / n_a
        print(
            f"  {label:<26}  n_with={n_a:>4}  n_without={n_b:>4}  (×{growth:.2f})   "
            f"DR_with={dr_a:>5.1f}%  DR_without={dr_b:>5.1f}%   "
            f"ΔDR={dr_pt:>+6.2f}pp CI[{dr_lo:>+6.2f}, {dr_hi:>+6.2f}]   "
            f"Δmean={mn_pt:>+5.2f}pp CI[{mn_lo:>+5.2f}, {mn_hi:>+5.2f}]"
        )
        return dict(n_a=n_a, n_b=n_b, growth=growth,
                    dr_pt=dr_pt, dr_lo=dr_lo, dr_hi=dr_hi,
                    mn_pt=mn_pt, mn_lo=mn_lo, mn_hi=mn_hi)

    print("\n— AND-gate stratification (primary) —")
    and_high = _cell("AND-HIGH (both Q1)",
                     lambda f: f.and_high, lambda f: f.and_high)
    and_off  = _cell("AND-OFF (not both Q1)",
                     lambda f: not f.and_high, lambda f: not f.and_high)
    all_     = _cell("ALL fires",
                     lambda f: True, lambda f: True)

    print("\n— Sub-stratification (rev_nhi alone / SMA50 alone) —")
    _cell("rev_nhi HIGH only",
          lambda f: f.nhi_high, lambda f: f.nhi_high)
    _cell("SMA50 HIGH only",
          lambda f: f.sma_high, lambda f: f.sma_high)

    print("\n" + "="*100)
    print("PRE-REGISTERED AND-HIGH GATE")
    print("="*100)
    if and_high is None:
        print("  Insufficient n in AND-HIGH cell.")
        return
    g_ok       = and_high["growth"] >= 1.5
    drop_ok    = (not math.isnan(and_high["dr_lo"])) and and_high["dr_lo"] >= -1.0
    keep_real  = (not math.isnan(and_high["dr_hi"])) and and_high["dr_hi"] < -1.5
    print(f"  AND-HIGH n grew ≥ 1.5×?               {'✓' if g_ok else '✗'}  ({and_high['growth']:.2f})")
    print(f"  AND-HIGH ΔDR CI lower ≥ -1.0pp?       {'✓' if drop_ok else '✗'}  ({and_high['dr_lo']:+.2f})")
    print(f"  AND-HIGH ΔDR CI upper < -1.5pp (filter helping)? "
          f"{'✓' if keep_real else '✗'}  ({and_high['dr_hi']:+.2f})")
    print()
    if keep_real:
        print("  → KEEP FILTER (real work in concentrated regime)")
    elif g_ok and drop_ok:
        print("  → DROP FILTER (conditional benefit confirmed in AND-HIGH cell)")
    else:
        print("  → STILL AMBIGUOUS — defer; today's verdict stands (keep filter).")
    print("="*100 + "\n")


if __name__ == "__main__":
    main()
