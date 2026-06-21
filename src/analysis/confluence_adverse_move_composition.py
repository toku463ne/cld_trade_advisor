"""Mechanism check for the adverse-move Stage-0: WHAT signs populate the dr<=-3% cohort?

Tests the objection (2026-06-21): a signal-day bar that drops >=3% to a new low far
below any recent trough should NOT be a valid rev_lo/rev_nlo fire — so the "trough-
catchers fire on down bars" story can't be what makes the adverse cohort outperform.

For every confluence signal day (>=3 valid bullish signs) in FY2018-FY2025, on each
stock's own calendar, compute the signal-bar return dr = close[T]/close[T-1]-1 and,
for each VALID sign, its staleness = T - (most recent fire index still inside its
3-5 bar window). staleness 0 = fired ON bar T; >=1 = a windowed leftover.

Reports, for the ADVERSE cohort (dr<=-3%) vs ALL signal days:
  - per-sign "valid" frequency
  - per-sign "fresh (fired ON bar T)" frequency
  - share of adverse days where every valid sign is STALE (pure leftover breakouts)
  - share where a reversal sign (rev_lo/rev_nlo/str_lead) is FRESH on bar T

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_adverse_move_composition
"""
from __future__ import annotations

import datetime
from collections import defaultdict

import numpy as np
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.confluence_benchmark import _FYS
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import _BULLISH_SIGNS, _VALID_BARS

_N_GATE = 3
_REVERSAL = {"rev_lo", "rev_nlo", "str_lead"}   # the "catch a turn" members


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH_SIGNS)))).all()
    fires_by_code: dict[str, list[tuple[str, datetime.date]]] = defaultdict(list)
    for sg, st, fa in rows:
        fires_by_code[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    adv_valid: defaultdict[str, int] = defaultdict(int)
    adv_fresh: defaultdict[str, int] = defaultdict(int)
    all_valid: defaultdict[str, int] = defaultdict(int)
    n_adv = n_all = 0
    adv_all_stale = 0          # adverse days where every valid sign staleness>=1
    adv_rev_fresh = 0          # adverse days with a reversal sign fresh on T
    adv_dr: list[float] = []

    seen_fy: set[str] = set()
    for cfg in _FYS:
        if cfg.stock_set in seen_fy:
            pass
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 90)
        se = cfg.end + datetime.timedelta(days=10)
        with get_session() as s:
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c

        for code, c in caches.items():
            # de-dup to one bar/day
            closes: dict[datetime.date, float] = {}
            cal: list[datetime.date] = []
            seen: set[datetime.date] = set()
            for b in c.bars:
                d = b.dt.date()
                if d in seen:
                    continue
                seen.add(d); closes[d] = b.close; cal.append(d)
            cal.sort()
            idx = {d: i for i, d in enumerate(cal)}

            # per-sign fire indices on this calendar
            fire_idx: dict[str, list[int]] = defaultdict(list)
            for sg, fd in fires_by_code.get(code, []):
                if fd in idx:
                    fire_idx[sg].append(idx[fd])
            for sg in fire_idx:
                fire_idx[sg].sort()

            for i, d in enumerate(cal):
                if d < cfg.start or d > cfg.end or i < 1:
                    continue
                # valid signs at T and their min staleness
                stale: dict[str, int] = {}
                for sg in _BULLISH_SIGNS:
                    vb = _VALID_BARS.get(sg, 5)
                    best = None
                    for fj in fire_idx.get(sg, []):
                        if fj <= i <= fj + vb:
                            best = i - fj    # smaller = fresher; keep last (closest)
                    if best is not None:
                        stale[sg] = best
                if len(stale) < _N_GATE:
                    continue
                cpre = closes[cal[i - 1]]
                if cpre <= 0:
                    continue
                dr = closes[d] / cpre - 1.0
                n_all += 1
                for sg in stale:
                    all_valid[sg] += 1
                if dr <= -0.03:
                    n_adv += 1
                    adv_dr.append(dr)
                    for sg, st_ in stale.items():
                        adv_valid[sg] += 1
                        if st_ == 0:
                            adv_fresh[sg] += 1
                    if all(v >= 1 for v in stale.values()):
                        adv_all_stale += 1
                    if any(sg in _REVERSAL and st_ == 0 for sg, st_ in stale.items()):
                        adv_rev_fresh += 1

    _report(adv_valid, adv_fresh, all_valid, n_adv, n_all, adv_all_stale,
            adv_rev_fresh, adv_dr)


def _report(adv_valid, adv_fresh, all_valid, n_adv, n_all, adv_all_stale,
            adv_rev_fresh, adv_dr) -> None:
    print("\n=== Adverse-move cohort COMPOSITION (raw confluence signal days, FY2018-FY2025) ===")
    print(f"signal days >=3 valid signs: {n_all}   of which ADVERSE (dr<=-3%): {n_adv} "
          f"({n_adv/n_all*100:.1f}%)")
    if adv_dr:
        print(f"adverse cohort mean signal-bar dr = {np.mean(adv_dr)*100:+.2f}% "
              f"(median {np.median(adv_dr)*100:+.2f}%)\n")

    print(f"{'sign':>14} | valid% adv | FRESH-on-T% adv | valid% all | (fresh/valid in adv)")
    for sg in _BULLISH_SIGNS:
        v = adv_valid.get(sg, 0); f = adv_fresh.get(sg, 0); va = all_valid.get(sg, 0)
        vp = v / n_adv * 100 if n_adv else 0
        fp = f / n_adv * 100 if n_adv else 0
        vap = va / n_all * 100 if n_all else 0
        frac = f / v * 100 if v else 0
        tag = " <-reversal" if sg in _REVERSAL else ""
        print(f"{sg:>14} | {vp:8.1f}   | {fp:11.1f}     | {vap:8.1f}   |  {frac:5.1f}%{tag}")

    print()
    print(f"adverse days where EVERY valid sign is STALE (leftover, none fired on T): "
          f"{adv_all_stale}/{n_adv} = {adv_all_stale/n_adv*100:.1f}%" if n_adv else "n/a")
    print(f"adverse days where a REVERSAL sign (rev_lo/rev_nlo/str_lead) is FRESH on T: "
          f"{adv_rev_fresh}/{n_adv} = {adv_rev_fresh/n_adv*100:.1f}%" if n_adv else "n/a")
    print()


if __name__ == "__main__":
    run()
