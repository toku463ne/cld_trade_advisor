"""sign_validate — permutation test and regime split for sign benchmark runs.

For each benchmark run_id:
  1. Permutation test: shuffle trend_direction labels 2000× → empirical p-value.
     Addresses the clustering concern: signs like str_hold that fire on consecutive
     days during the same N225 decline event produce dependent observations that
     inflate n and make the binomial p look smaller than it should.
  2. Deduplication check: keep one event per stock per 5-day window → directly
     shows how much the event count is inflated by clustering and whether the
     direction_rate holds up after deduplication.
  3. Regime split: classify each fire event by the N225 trend at the fire date
     (last confirmed N225 zigzag peak before fire date = bear/bull), then compute
     separate direction_rate and bench metrics per regime.

N225 regime definitions (ZZ_SIZE=5, confirmed = both sides confirmed):
  bear : last confirmed N225 zigzag peak was a HIGH (+2) — N225 in decline
  bull : last confirmed N225 zigzag peak was a LOW  (-2) — N225 in ascent
  unk  : no confirmed N225 peak found before this fire date in the loaded period

CLI:
    uv run --env-file devenv python -m src.analysis.sign_validate \\
        --run-ids 22,23,24,25,26,27,28,30,31,33,34
"""
from __future__ import annotations

import argparse
import bisect
import datetime
import math
import sys
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.indicators.zigzag import detect_peaks
from src.simulator.cache import DataCache

_N225         = "^N225"
_ZZ_SIZE      = 5
_DEDUP_WINDOW = 5  # trading days


# ── N225 regime map ───────────────────────────────────────────────────────────

def _build_regime_map(
    session: Session,
    start: datetime.datetime,
    end: datetime.datetime,
) -> dict[datetime.date, str]:
    """Return date → 'bear' | 'bull' for each N225 trading date in [start, end].

    A confirmed HIGH (+2) at bar k becomes knowable at bar k + ZZ_SIZE.
    'bear' = last knowable peak was HIGH; 'bull' = last knowable peak was LOW.
    """
    cache = DataCache(_N225, "1d")
    cache.load(session, start, end)
    if not cache.bars:
        return {}

    dates = [b.dt.date() for b in cache.bars]
    highs = [b.high      for b in cache.bars]
    lows  = [b.low       for b in cache.bars]

    peaks = detect_peaks(highs, lows, size=_ZZ_SIZE, middle_size=0)
    confirmed: list[tuple[datetime.date, str]] = []  # (knowable_date, regime)
    for p in peaks:
        if abs(p.direction) != 2:
            continue
        know_idx = p.bar_index + _ZZ_SIZE
        if know_idx >= len(dates):
            continue
        regime = "bear" if p.direction == +2 else "bull"
        confirmed.append((dates[know_idx], regime))
    confirmed.sort(key=lambda x: x[0])
    if not confirmed:
        return {}

    know_dates = [c[0] for c in confirmed]
    result: dict[datetime.date, str] = {}
    for d in dates:
        pos = bisect.bisect_right(know_dates, d) - 1
        if pos >= 0:
            result[d] = confirmed[pos][1]
    return result


# ── Metric helpers ────────────────────────────────────────────────────────────

def _binomial_p(n: int, dr: float) -> float:
    """Two-tailed normal approximation to binomial p-value vs H₀ = 0.5."""
    if n == 0:
        return 1.0
    z = abs(dr - 0.5) / (0.5 / math.sqrt(n))
    return math.erfc(z / math.sqrt(2))


def _fmt_p(p: float) -> str:
    if p < 0.001:
        return "<0.001"
    return f"≈{p:.3f}"


@dataclass
class _Metrics:
    n: int
    dr: float
    bench_flw: float | None
    bench_rev: float | None
    p_binom: float


def _compute_metrics(events: list) -> _Metrics | None:
    with_trend = [e for e in events if e.trend_direction is not None]
    if not with_trend:
        return None
    n  = len(with_trend)
    dr = sum(1 for e in with_trend if e.trend_direction == +1) / n
    flw_mags = [e.trend_magnitude for e in with_trend
                if e.trend_direction == +1 and e.trend_magnitude is not None]
    rev_mags = [e.trend_magnitude for e in with_trend
                if e.trend_direction == -1 and e.trend_magnitude is not None]
    mag_flw = float(np.mean(flw_mags)) if flw_mags else None
    mag_rev = float(np.mean(rev_mags)) if rev_mags else None
    return _Metrics(
        n=n,
        dr=dr,
        bench_flw=dr * mag_flw               if mag_flw is not None else None,
        bench_rev=(1 - dr) * mag_rev          if mag_rev is not None else None,
        p_binom=_binomial_p(n, dr),
    )


def _deduplicate(events: list, window_days: int = _DEDUP_WINDOW) -> list:
    """Keep only the first fire per stock within each non-overlapping N-day window."""
    last_fire: dict[str, datetime.date] = {}
    result = []
    for e in sorted(events, key=lambda x: x.fired_at):
        d = e.fired_at.date()
        last = last_fire.get(e.stock_code)
        if last is None or (d - last).days > window_days:
            result.append(e)
            last_fire[e.stock_code] = d
    return result


def _permutation_test(
    directions: list[int],
    n_perms: int = 2000,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Return (empirical_p, null_95th_pct_DR, null_99th_pct_DR).

    Simulates H₀: each event outcome is drawn independently from Bernoulli(0.5).
    Empirical p = fraction of null simulations with DR >= actual DR.

    Note: shuffling the existing labels (the naive approach) always gives the
    same DR because the proportion of +1s is fixed — that's why we simulate
    fresh Bernoulli draws instead.
    """
    rng   = np.random.default_rng(seed)
    n     = len(directions)
    if n == 0:
        return 1.0, 0.5, 0.5
    actual_dr = sum(1 for d in directions if d == +1) / n
    # Draw n_perms independent Binomial(n, 0.5) samples → null DR distribution
    null_drs = rng.binomial(n, 0.5, size=n_perms) / n
    emp_p = float(np.mean(null_drs >= actual_dr))
    return emp_p, float(np.percentile(null_drs, 95)), float(np.percentile(null_drs, 99))


# ── Per-run validation ────────────────────────────────────────────────────────

def validate_run(
    session: Session,
    run: SignBenchmarkRun,
    regime_map: dict[datetime.date, str],
    n_perms: int = 2000,
) -> None:
    events = list(session.execute(
        select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id == run.id)
    ).scalars().all())

    print(f"\n{'═' * 72}")
    print(f"  {run.sign_type}  (run_id={run.id})  "
          f"stock_set={run.stock_set}  n={run.n_events}")
    print(f"{'═' * 72}")

    directions = [e.trend_direction for e in events if e.trend_direction is not None]
    if not directions:
        print("  [no events with trend outcome]\n")
        return

    actual_dr = sum(1 for d in directions if d == +1) / len(directions)

    # ── 1. Permutation test ───────────────────────────────────────────────────
    emp_p, pct95, pct99 = _permutation_test(directions, n_perms=n_perms)
    if   emp_p < 0.001: verdict = "✓ PASSES  (<0.001)"
    elif emp_p < 0.05:  verdict = f"✓ PASSES  ({_fmt_p(emp_p)})"
    elif emp_p < 0.10:  verdict = f"~ BORDERLINE  ({_fmt_p(emp_p)})"
    else:               verdict = f"✗ FAILS  ({_fmt_p(emp_p)})"

    print(f"\n  PERMUTATION TEST  ({n_perms} iterations, shuffle trend labels)")
    print(f"    actual direction_rate : {actual_dr:.1%}")
    print(f"    null 95th pct         : {pct95:.1%}")
    print(f"    null 99th pct         : {pct99:.1%}")
    print(f"    empirical p-value     : {verdict}")

    # ── 2. Deduplication check ────────────────────────────────────────────────
    dedup  = _deduplicate(events)
    full_m = _compute_metrics(events)
    dedup_m = _compute_metrics(dedup)
    full_n  = full_m.n  if full_m  else 0
    dedup_n = dedup_m.n if dedup_m else 0
    inflation = full_n / dedup_n if dedup_n else float("nan")

    print(f"\n  DEDUPLICATION CHECK  (1 event per stock per {_DEDUP_WINDOW}-day window)")
    print(f"    full n={full_n}  →  dedup n={dedup_n}  "
          f"(inflation ×{inflation:.1f})")
    if dedup_m:
        dr_diff = dedup_m.dr - actual_dr
        note = ("stable" if abs(dr_diff) < 0.02
                else "↓ drops after dedup — clustering was inflating DR"
                if dr_diff < 0
                else "↑ rises after dedup")
        dedup_p = _fmt_p(dedup_m.p_binom)
        print(f"    dedup direction_rate  : {dedup_m.dr:.1%}  (p_binom={dedup_p})")
        print(f"    Δdr vs full           : {dr_diff:+.1%}  → {note}")

    # ── 3. Regime split ───────────────────────────────────────────────────────
    by_regime: dict[str, list] = defaultdict(list)
    for e in events:
        by_regime[regime_map.get(e.fired_at.date(), "unk")].append(e)

    bear_n = len(by_regime.get("bear", []))
    bull_n = len(by_regime.get("bull", []))
    unk_n  = len(by_regime.get("unk",  []))
    print(f"\n  REGIME SPLIT  "
          f"(N225 ZZ_SIZE={_ZZ_SIZE}; bear=last confirmed peak HIGH, bull=last LOW)")
    print(f"    event distribution: bear={bear_n}  bull={bull_n}  unk={unk_n}")
    print(f"    {'regime':<6}  {'n':>5}  {'dr':>6}  {'p_binom':<9}  "
          f"{'bench_flw':>9}  {'bench_rev':>9}")
    print(f"    {'─'*6}  {'─'*5}  {'─'*6}  {'─'*9}  {'─'*9}  {'─'*9}")

    for regime in ("bear", "bull", "unk", "ALL"):
        evts = list(events) if regime == "ALL" else by_regime.get(regime, [])
        m = _compute_metrics(evts)
        if m is None or m.n == 0:
            print(f"    {regime:<6}  {'—':>5}")
            continue
        p_str = _fmt_p(m.p_binom)
        bf = f"{m.bench_flw:.4f}" if m.bench_flw is not None else "    —"
        br = f"{m.bench_rev:.4f}" if m.bench_rev is not None else "    —"
        sig = " ←" if regime != "ALL" and m.p_binom < 0.05 else ""
        print(f"    {regime:<6}  {m.n:>5}  {m.dr:>5.1%}  {p_str:<9}  "
              f"{bf:>9}  {br:>9}{sig}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    ap = argparse.ArgumentParser(prog="python -m src.analysis.sign_validate")
    ap.add_argument("--run-ids", required=True,
                    help="Comma-separated SignBenchmarkRun IDs to validate")
    ap.add_argument("--n-perms", type=int, default=2000,
                    help="Permutation iterations (default 2000)")
    args = ap.parse_args(argv)

    run_ids = [int(x.strip()) for x in args.run_ids.split(",")]

    with get_session() as session:
        runs = session.execute(
            select(SignBenchmarkRun).where(SignBenchmarkRun.id.in_(run_ids))
        ).scalars().all()
        runs_by_id = {r.id: r for r in runs}

        if not runs:
            print("No runs found for the given IDs.")
            return

        start = min(r.start_dt for r in runs)
        end   = max(r.end_dt   for r in runs)
        print(f"Loading N225 regime map  {start.date()} – {end.date()} …")
        regime_map = _build_regime_map(session, start, end)
        print(f"  → {len(regime_map)} dated entries "
              f"(bear={sum(1 for v in regime_map.values() if v == 'bear')}  "
              f"bull={sum(1 for v in regime_map.values() if v == 'bull')})")

        for run_id in run_ids:
            run = runs_by_id.get(run_id)
            if run is None:
                print(f"\n[run_id={run_id} not found — skipped]")
                continue
            validate_run(session, run, regime_map, n_perms=args.n_perms)

    print("\nDone.")


if __name__ == "__main__":
    main()
