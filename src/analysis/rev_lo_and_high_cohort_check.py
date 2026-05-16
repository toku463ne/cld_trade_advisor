"""rev_lo × AND-HIGH per-cohort robustness check.

The pooled FY2024+FY2025+2026YTD regime probe (rev_lo_filter_regime_ab.py)
found rev_lo's direction rate drops from ~60% in AND-OFF cells to
~38% in AND-HIGH cells (n=80, binomial 95% CI [26.9%, 48.1%] —
upper bound below 50%).

Before shipping a BREADTH_VETO entry for rev_lo, verify the
collapse holds in each cohort individually.  Pre-registered (locked
before running):

  - **SHIP** if 3-of-3 cohorts (FY2024, FY2025, 2026 YTD) show
    AND-HIGH DR below 50% (point estimate) AND AND-HIGH DR strictly
    lower than the same cohort's AND-OFF DR.
  - **DON'T SHIP** if any single cohort shows AND-HIGH DR ≥ 50%
    OR AND-HIGH DR ≥ same-cohort AND-OFF DR — the gate is then
    cohort-dependent / 2026-specific and should not be added to
    production.

Run:
    uv run --env-file devenv python -m src.analysis.rev_lo_and_high_cohort_check
"""

from __future__ import annotations

import datetime
import math
from collections import defaultdict

from loguru import logger

from src.analysis.exit_benchmark import _load_rep_codes
from src.data.db import get_session
from src.indicators.rev_n_regime import RevNRegime
from src.indicators.sma_regime import SMARegime
from src.signs.rev_peak import RevPeakDetector
from src.simulator.cache import DataCache

HORIZON = 10

tz = datetime.timezone.utc
END_DT      = datetime.datetime(2026, 5, 15, 23, 59, 59, tzinfo=tz)
BUILD_START = datetime.datetime(2023, 3, 1, tzinfo=tz)

COHORTS = [
    (datetime.date(2024, 4, 1), datetime.date(2025, 3, 31), "FY2024"),
    (datetime.date(2025, 4, 1), datetime.date(2026, 3, 31), "FY2025"),
    (datetime.date(2026, 1, 1), datetime.date(2026, 5, 15), "2026YTD"),
    (datetime.date(2023, 4, 1), datetime.date(2026, 5, 15), "ALL"),
]


def _fwd_return(bymd, sdates, fire_date, h=HORIZON):
    try:
        i = sdates.index(fire_date)
    except ValueError:
        return None
    if i + h + 1 >= len(sdates):
        return None
    entry = bymd[sdates[i + 1]][0].open
    exit_ = bymd[sdates[i + 1 + h]][-1].close
    if entry is None or exit_ is None or entry <= 0:
        return None
    return (float(exit_) - float(entry)) / float(entry)


def _binomial_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% CI on a proportion — better than normal approx at small n."""
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    width  = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - width), min(1.0, centre + width)


def main() -> None:
    rep_codes = _load_rep_codes("classified2024")
    logger.info("Universe: {} stocks", len(rep_codes))
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

    all_dates = sorted({b.dt.date() for c in caches.values() for b in c.bars})
    logger.info("Building RevNRegime + SMARegime over {} dates …", len(all_dates))
    rev_regime = RevNRegime.build(caches, all_dates)
    sma_regime = SMARegime.build(caches, all_dates)
    is_and_high = {d: (rev_regime.is_high(d) and sma_regime.is_high(d))
                   for d in all_dates}

    # Collect fires (production filter; same as today's rev_lo)
    fires: list[tuple[datetime.date, bool, float]] = []  # (date, and_high, fwd_ret)
    for code, cache in caches.items():
        bymd: dict = defaultdict(list)
        for b in cache.bars:
            bymd[b.dt.date()].append(b)
        sdates = sorted(bymd)
        det = RevPeakDetector(cache, side="lo")
        for fi, _score in det._fire_events:
            fire_date = cache.bars[fi].dt.date()
            fr = _fwd_return(bymd, sdates, fire_date)
            if fr is None: continue
            fires.append((fire_date, is_and_high.get(fire_date, False), fr))

    logger.info("Total rev_lo fires with forward return: {}", len(fires))

    print("\n" + "="*94)
    print("REV_LO × AND-HIGH PER-COHORT ROBUSTNESS — rev_lo direction rate by regime cell")
    print("="*94)

    def _cohort_split(c_start: datetime.date, c_end: datetime.date) -> tuple[int, int, int, int]:
        """Return (n_high, k_high_wins, n_off, k_off_wins) for the cohort."""
        n_h = k_h = n_o = k_o = 0
        for fd, ah, fr in fires:
            if fd < c_start or fd > c_end:
                continue
            win = fr > 0
            if ah:
                n_h += 1
                if win: k_h += 1
            else:
                n_o += 1
                if win: k_o += 1
        return n_h, k_h, n_o, k_o

    print(f"  {'cohort':<10}  {'AND-HIGH n':>10}  {'AND-HIGH DR':>12}  {'95% CI':>20}  "
          f"{'AND-OFF n':>10}  {'AND-OFF DR':>11}  {'95% CI':>20}  Δ(off−high)")
    print("  " + "-"*92)

    verdicts = {}
    for c_start, c_end, lbl in COHORTS:
        n_h, k_h, n_o, k_o = _cohort_split(c_start, c_end)
        if n_h < 2 or n_o < 2:
            print(f"  {lbl:<10}  (insufficient n: AND-HIGH={n_h}, AND-OFF={n_o})")
            verdicts[lbl] = None
            continue
        dr_h, dr_o = k_h / n_h, k_o / n_o
        ci_h_lo, ci_h_hi = _binomial_ci(k_h, n_h)
        ci_o_lo, ci_o_hi = _binomial_ci(k_o, n_o)
        delta = dr_o - dr_h
        ci_h_str = f"[{ci_h_lo*100:5.1f}, {ci_h_hi*100:5.1f}]"
        ci_o_str = f"[{ci_o_lo*100:5.1f}, {ci_o_hi*100:5.1f}]"
        print(f"  {lbl:<10}  {n_h:>10}  {dr_h*100:>11.1f}%  {ci_h_str:>20}  "
              f"{n_o:>10}  {dr_o*100:>10.1f}%  {ci_o_str:>20}  {delta*100:>+6.1f}pp")
        verdicts[lbl] = (n_h, dr_h, ci_h_hi, dr_o)

    # ── Pre-registered gate ──
    print("\n" + "="*94)
    print("PRE-REGISTERED GATE")
    print("="*94)
    print("  Per-cohort PASS if: AND-HIGH DR < 50% AND AND-HIGH DR < AND-OFF DR (same cohort)")

    individuals = [k for k in verdicts if k != "ALL" and verdicts[k] is not None]
    pass_count = 0
    for lbl in individuals:
        n_h, dr_h, ci_h_hi, dr_o = verdicts[lbl]
        cond_below_50  = dr_h < 0.50
        cond_below_off = dr_h < dr_o
        ok = cond_below_50 and cond_below_off
        if ok: pass_count += 1
        check = "✓" if ok else "✗"
        print(f"  {lbl:<10}  AND-HIGH DR = {dr_h*100:.1f}% "
              f"{'< 50%' if cond_below_50 else '≥ 50%'}, "
              f"{'< AND-OFF' if cond_below_off else '≥ AND-OFF'}  {check}")
    print(f"\n  Cohorts passing: {pass_count} / {len(individuals)}")

    if pass_count == len(individuals) and len(individuals) >= 3:
        print("\n  → SHIP: rev_lo × AND-HIGH gate is per-cohort robust.")
        print("    Add to BREADTH_VETO in src/analysis/regime_ranking.py.")
    elif pass_count >= len(individuals) - 1:
        print(f"\n  → BORDERLINE: {pass_count}/{len(individuals)} cohorts pass.")
        print("    Consider shipping but with explicit per-cohort caveat in docstring.")
    else:
        print(f"\n  → DON'T SHIP: only {pass_count}/{len(individuals)} cohorts pass.")
        print("    Gate is cohort-dependent; treat aggregate finding as 2026-specific artifact.")
    print("="*94 + "\n")


if __name__ == "__main__":
    main()
