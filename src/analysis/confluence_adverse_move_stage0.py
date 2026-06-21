"""Adverse-move-at-entry veto on the canonical confluence book — Stage-0 decomposition.

Origin (2026-06-21): a live conf3 proposal on 2432.T stacked two STALE windowed
breakouts (brk_tenkan_hi fired 06-15, brk_kumo_hi 06-17) with a fresh str_lead on
a −4% down bar (06-18); the two-bar fill would buy 06-19's open straight into a
−6% gap-down crash. The validity window (3-5 bars) lets a breakout count days
after the bar that triggered it, so the decision/fill bar can be a falling knife.

Distinct from the level-persistence "still-holds" gate (REJECTED, Δ −0.062) — that
asked whether the breakout level still held; this asks the narrower question:
does a large ADVERSE PRICE MOVE on the bar we act on predict a worse fill?

Two causal features, both known by the opening-auction fill (no look-ahead):
  - dr_dec = close[T]/close[T-1] − 1      (decision-bar return; T = signal day,
            the trading day BEFORE the fill — known the evening we decide)
  - gap    = open[T+1]/close[T] − 1        (fill gap = entry_price/close[T] − 1 —
            known at the auction where we actually fill)

Per fill we bucket forward total return (exit/entry−1) by dr_dec and by gap, pooled
over FY2018–FY2025 (FY2025 = OOS) × K fill-order shuffles on the production 6-slot
book.

Pre-stated Stage-0 gates (decided BEFORE running):
  - ESCALATE to the paired fill-order null ONLY if ALL hold:
      (1) ADVERSE (dr_dec ≤ −3%) − CALM (dr_dec ≥ 0) mean_r spread ≤ −0.5pp,
      (2) monotone: bucket mean_r non-decreasing from most-adverse to least,
      (3) ADVERSE win% < CALM win%,
      (4) sign-consistent: ADVERSE−CALM spread negative in ≥6/8 FYs.
  - If the adverse cohort is NOT worse (or is BETTER — falling knives that bounce,
    which is what str_lead/rev_lo are built to catch), REJECT: a veto would skip
    winners (the limit-entry / still-holds lesson). Descriptive only, write memory.

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_adverse_move_stage0
"""
from __future__ import annotations

import datetime
import random
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_benchmark import _FYS
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import _BULLISH_SIGNS

_SLOTS = 6
_N_GATE = 3
_K_ORDERS = 20

# dr_dec buckets (decision-bar return), most-adverse → least
_DR_BUCKETS = [
    (-1.00, -0.030, "dr<=-3%"),
    (-0.030, -0.015, "dr -3..-1.5%"),
    (-0.015,  0.000, "dr -1.5..0%"),
    (0.000,  0.015, "dr 0..1.5%"),
    (0.015,  1.00, "dr >1.5%"),
]
# gap buckets (fill gap), most-adverse → least
_GAP_BUCKETS = [
    (-1.00, -0.020, "gap<=-2%"),
    (-0.020, -0.005, "gap -2..-0.5%"),
    (-0.005,  0.005, "gap -0.5..0.5%"),
    (0.005,  0.020, "gap 0.5..2%"),
    (0.020,  1.00, "gap >2%"),
]


def _bucket(val: float, buckets) -> str:
    for lo, hi, lab in buckets:
        if lo < val <= hi:
            return lab
    # val exactly at the most-negative edge (or below) → first bucket
    return buckets[0][2] if val <= buckets[0][1] else buckets[-1][2]


def _coh_line(name: str, rets: list[float]) -> str:
    if not rets:
        return f"  {name:>16}: n=0"
    a = np.asarray(rets)
    return (f"  {name:>16}: n={len(a):>6}  mean_r={a.mean()*100:+.2f}%  "
            f"win%={float((a > 0).mean()*100):.1f}%  med={np.median(a)*100:+.2f}%")


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    exsim._MAX_LOW_CORR = 5     # production 6-slot

    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH_SIGNS)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    dr_buckets: defaultdict[str, list[float]] = defaultdict(list)
    gap_buckets: defaultdict[str, list[float]] = defaultdict(list)
    per_fy: dict[str, dict] = {}
    tot_fills = 0
    adverse_fills = 0

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 90)
        se = cfg.end + datetime.timedelta(days=60)
        with get_session() as s:
            n225 = DataCache("^N225", "1d")
            n225.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                      datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
            caches = {}
            for code in codes:
                c = DataCache(code, "1d")
                c.load(s, datetime.datetime.combine(ss, datetime.time.min, tzinfo=datetime.timezone.utc),
                       datetime.datetime.combine(se, datetime.time.max, tzinfo=datetime.timezone.utc))
                if c.bars:
                    caches[code] = c
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        # per-stock date->bar map + calendar (for decision-bar / gap features)
        bar_of = {code: {b.dt.date(): b for b in c.bars} for code, c in caches.items()}
        cal_of = {code: sorted(bar_of[code]) for code in caches}
        ix_of = {code: {d: i for i, d in enumerate(cal)} for code, cal in cal_of.items()}

        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))

        fy_adv: list[float] = []
        fy_calm: list[float] = []
        fy_fills = 0
        for seed in range(_K_ORDERS):
            pool = cands[:]
            random.Random(seed).shuffle(pool)
            results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
            for p in results:
                if not p.entry_price:
                    continue
                ret = p.exit_price / p.entry_price - 1.0
                code = p.stock_code
                cal = cal_of.get(code, [])
                # NB: ExitResult.entry_date is the SIGNAL day T (exit_simulator sets
                # entry_date=candidate.entry_date, entry_price=open[T+1]). So the
                # decision bar IS p.entry_date; T-1 is the prior trading day.
                ei = ix_of.get(code, {}).get(p.entry_date)   # = T
                if ei is None or ei < 1:
                    continue
                t_pre = cal[ei - 1]                          # T-1
                c_dec = bar_of[code][p.entry_date].close     # close[T]
                c_pre = bar_of[code][t_pre].close            # close[T-1]
                if c_dec <= 0 or c_pre <= 0:
                    continue
                dr_dec = c_dec / c_pre - 1.0                 # signal-bar return
                gap = p.entry_price / c_dec - 1.0            # open[T+1]/close[T] - 1
                dr_buckets[_bucket(dr_dec, _DR_BUCKETS)].append(ret)
                gap_buckets[_bucket(gap, _GAP_BUCKETS)].append(ret)
                fy_fills += 1
                if dr_dec <= -0.03:
                    fy_adv.append(ret); adverse_fills += 1
                elif dr_dec >= 0.0:
                    fy_calm.append(ret)
        tot_fills += fy_fills
        per_fy[cfg.label] = {
            "fills": fy_fills / _K_ORDERS,
            "adv_mean": float(np.mean(fy_adv)) if fy_adv else float("nan"),
            "calm_mean": float(np.mean(fy_calm)) if fy_calm else float("nan"),
            "adv_n": len(fy_adv) / _K_ORDERS,
        }
        logger.info("  {} done ({} cands, {:.0f} fills/order, adv {:.0f}/order)",
                    cfg.label, len(cands), fy_fills / _K_ORDERS,
                    len(fy_adv) / _K_ORDERS)

    _report(dr_buckets, gap_buckets, per_fy, tot_fills, adverse_fills)


def _report(dr_buckets, gap_buckets, per_fy, tot_fills, adverse_fills) -> None:
    print("\n=== Adverse-move-at-entry — Stage-0 (canonical 6-slot confluence book) ===")
    print(f"params: K_ORDERS={_K_ORDERS}  N_GATE={_N_GATE}  SLOTS={_SLOTS}  "
          f"FY2018-FY2025 (FY2025=OOS)\n")

    print("(A) forward return by DECISION-BAR return (close[T]/close[T-1]-1), "
          f"pooled over FYs x {_K_ORDERS} orders:")
    for _, _, lab in _DR_BUCKETS:
        print(_coh_line(lab, dr_buckets.get(lab, [])))

    print("\n(B) forward return by FILL GAP (open[T+1]/close[T]-1):")
    for _, _, lab in _GAP_BUCKETS:
        print(_coh_line(lab, gap_buckets.get(lab, [])))

    # binding contrast ADVERSE (dr<=-3%) vs CALM (dr>=0)
    adv = dr_buckets.get(_DR_BUCKETS[0][2], [])
    calm = dr_buckets.get(_DR_BUCKETS[3][2], []) + dr_buckets.get(_DR_BUCKETS[4][2], [])
    print("\n(C) binding contrast — ADVERSE(dr<=-3%) vs CALM(dr>=0):")
    print(_coh_line("ADVERSE", adv))
    print(_coh_line("CALM", calm))
    spread = (np.mean(adv) - np.mean(calm)) * 100 if (adv and calm) else float("nan")
    dr_win = float((np.asarray(adv) > 0).mean() * 100) if adv else float("nan")
    calm_win = float((np.asarray(calm) > 0).mean() * 100) if calm else float("nan")

    # monotonicity across the 5 dr buckets (most-adverse -> least)
    means = [np.mean(dr_buckets[lab]) if dr_buckets.get(lab) else np.nan
             for _, _, lab in _DR_BUCKETS]
    valid = [m for m in means if not np.isnan(m)]
    monotone = all(valid[i] <= valid[i + 1] + 1e-9 for i in range(len(valid) - 1))

    print("\n   per-FY ADVERSE−CALM mean_r spread (sign consistency):")
    neg_fy = pos_fy = 0
    for lab, d in per_fy.items():
        if np.isnan(d["adv_mean"]) or np.isnan(d["calm_mean"]):
            print(f"     {lab:<8} (insufficient adverse/calm fills; adv n/ord {d['adv_n']:.1f})")
            continue
        sp = (d["adv_mean"] - d["calm_mean"]) * 100
        neg_fy += sp < 0; pos_fy += sp > 0
        print(f"     {lab:<8} {sp:+6.2f}pp  (fills/ord {d['fills']:.0f}, adv/ord {d['adv_n']:.1f})")
    print(f"   sign consistency: {neg_fy} FYs negative / {pos_fy} positive")

    cov = adverse_fills / tot_fills * 100 if tot_fills else float("nan")
    print(f"\n(D) coarseness: a 'skip if dr_dec<=-3%' veto thins {cov:.1f}% of fills "
          f"({adverse_fills/_K_ORDERS:.0f}/order across all FYs)")

    print("\n(E) VERDICT (gates pre-stated in docstring):")
    g1 = (not np.isnan(spread)) and spread <= -0.5
    g2 = monotone
    g3 = (not np.isnan(dr_win)) and dr_win < calm_win
    g4 = neg_fy >= 6
    print(f"  (1) ADVERSE−CALM spread {spread:+.2f}pp  {'PASS' if g1 else 'FAIL'} (<= -0.5pp)")
    print(f"  (2) monotone most-adverse→least             {'PASS' if g2 else 'FAIL'}")
    print(f"  (3) ADVERSE win% {dr_win:.1f} < CALM {calm_win:.1f}  {'PASS' if g3 else 'FAIL'}")
    print(f"  (4) FY sign-consistency {neg_fy}/8 negative   {'PASS' if g4 else 'FAIL'} (>=6)")
    escalate = g1 and g2 and g3 and g4
    print(f"  → {'ESCALATE (paired fill-order null on the veto)' if escalate else 'REJECT — descriptive only, write memory and stop'}")
    print()


if __name__ == "__main__":
    run()
