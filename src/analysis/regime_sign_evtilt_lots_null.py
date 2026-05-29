"""RegimeSign EV-tilt under INTEGER-LOT granularity — the live-readiness check for backlog item 2.

`regime_sign_evtilt_null` + `regime_sign_evtilt_phase_null` established the neutral-momentum trim (τ=0.5)
on the IDEALIZED equal-weight book (each held name a fractional 1/6) — both binding nulls PASS, a ~4.8pp
drawdown lever. The live book is NOT fractional: it trades 単元株 (100-share lots) at ¥2,000,000 / 6 slots
≈ ¥333k/slot, so a name's weight is `position_weight(recommended_lots(...), price)` — whole lots, cash drag,
and names priced > ~¥3,333 skip the slot (`recommended_lots == 0`).

GRANULARITY RISK: "trim neutral to τ=0.5" realistically means buy `floor(0.5 × base_lots)` lots. Many
names have base_lots 1–2, so floor(0.5×1)=0 → a 1-lot neutral name ROUNDS TO ZERO (cash in that slot). The
realized trim is coarse and skews toward "skip" for cheap-slot names. This probe asks whether the idealized
Δ Sharpe survives the rounding. Mirrors `confluence_evtilt_lots_null.py`; only the shipped RegimeSign pool
(`build_fy_candidates`) and the frozen Stage-0 cutoffs differ.

Gate (same): P(Δ Sharpe>0) ≥ 0.95 AND 95% CI-lo > 0, maxDD not worsened — on the integer-lot book. Reports
the granularity bite (how often the trim rounds to 0; realized τ_eff vs nominal). Read-only.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.regime_sign_evtilt_lots_null
"""
from __future__ import annotations

import math
import random
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger

import src.exit.exit_simulator as exsim
from src.analysis.regime_sign_backtest import (
    EXIT_RULE,
    RS_FY_CONFIGS,
    build_fy_candidates,
)
from src.exit.exit_simulator import run_simulation
from src.portfolio.sizing import position_weight, recommended_lots

_K = 200
_SLOTS = 6
_BUDGET = 2_000_000
_N225_MOM = 60
_T1, _T2 = -0.0101, 0.0654   # FROZEN Stage-0 cutoffs (same as regime_sign_evtilt_null)
_TAU = 0.5


def _closes(cache):
    dts, cmap, seen = [], {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close
    dts.sort()
    return dts, cmap


def _pos_daily(p, dts, cmap):
    try:
        ie, ix = dts.index(p.entry_date), dts.index(p.exit_date)
    except ValueError:
        return {}
    out = {}
    if ie == ix:
        out[p.entry_date] = p.exit_price / p.entry_price - 1.0
        return out
    span = dts[ie:ix + 1]
    for k, d in enumerate(span):
        if k == 0:
            out[d] = cmap[d] / p.entry_price - 1.0
        elif d == p.exit_date:
            out[d] = p.exit_price / cmap[span[k - 1]] - 1.0
        else:
            out[d] = cmap[d] / cmap[span[k - 1]] - 1.0
    return out


def _regime_at(n_dts, n_idx, n_cmap, d):
    i = n_idx.get(d)
    if i is None or i < _N225_MOM:
        return "na"
    p0 = n_cmap[n_dts[i - _N225_MOM]]
    if not p0:
        return "na"
    m = n_cmap[d] / p0 - 1.0
    return "bear" if m <= _T1 else ("neutral" if m <= _T2 else "bull")


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _ret_dd(rets):
    if len(rets) < 2:
        return float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    runmax = np.maximum.accumulate(eq)
    return float(eq[-1] - 1.0), float((eq / runmax - 1.0).min())


def run() -> None:
    ew_st = [[] for _ in range(_K)]
    tl_st = [[] for _ in range(_K)]
    ew_oos = [[] for _ in range(_K)]
    tl_oos = [[] for _ in range(_K)]
    diag = {"neutral": 0, "trim_to_zero": 0, "base_lots_sum": 0, "tilt_lots_sum": 0}

    exsim._MAX_LOW_CORR = 5
    for cfg in RS_FY_CONFIGS:
        cs = build_fy_candidates(cfg)
        if not cs.candidates or cs.n225_cache is None:
            continue
        caches = cs.stock_caches
        n_dts, n_cmap = _closes(cs.n225_cache)
        n_idx = {d: i for i, d in enumerate(n_dts)}
        stock_dts = {code: _closes(c) for code, c in caches.items()}

        def _affordable(c) -> bool:
            _, cmap = stock_dts.get(c.stock_code, ([], {}))
            px = cmap.get(c.entry_date)
            return px is not None and recommended_lots(_BUDGET, float(px), _SLOTS) > 0
        cands = [c for c in cs.candidates if _affordable(c)]
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        regime_of = {(c.stock_code, c.entry_date):
                     _regime_at(n_dts, n_idx, n_cmap, c.entry_date) for c in cands}

        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)
            results = run_simulation(pool, EXIT_RULE, caches, cfg.end)
            cal_set = set(cal)
            ew_day, tl_day = defaultdict(float), defaultdict(float)
            for p in results:
                sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
                base_lots = recommended_lots(_BUDGET, float(p.entry_price), _SLOTS)
                reg = regime_of.get((p.stock_code, p.entry_date), "na")
                tilt_lots = int(_TAU * base_lots) if reg == "neutral" else base_lots
                w_ew = position_weight(base_lots, float(p.entry_price), _BUDGET)
                w_tl = position_weight(tilt_lots, float(p.entry_price), _BUDGET)
                if k == 0 and reg == "neutral":
                    diag["neutral"] += 1
                    diag["base_lots_sum"] += base_lots
                    diag["tilt_lots_sum"] += tilt_lots
                    if tilt_lots == 0:
                        diag["trim_to_zero"] += 1
                for d, r in _pos_daily(p, sdts, scmap).items():
                    if d in cal_set:
                        ew_day[d] += r * w_ew
                        tl_day[d] += r * w_tl
            ew_seq = [ew_day.get(d, 0.0) for d in cal]
            tl_seq = [tl_day.get(d, 0.0) for d in cal]
            ew_st[k] += ew_seq[1:]; tl_st[k] += tl_seq[1:]
            if cfg.label == "FY2025":
                ew_oos[k] += ew_seq[1:]; tl_oos[k] += tl_seq[1:]
        logger.info("  {} done ({} affordable candidates, {} shuffles)", cfg.label, len(cands), _K)

    ew_sh = np.array([_sharpe(ew_st[k]) for k in range(_K)])
    tl_sh = np.array([_sharpe(tl_st[k]) for k in range(_K)])
    ew_rd = np.array([_ret_dd(ew_st[k]) for k in range(_K)])
    tl_rd = np.array([_ret_dd(tl_st[k]) for k in range(_K)])
    ew_oo = np.array([_sharpe(ew_oos[k]) for k in range(_K)])
    tl_oo = np.array([_sharpe(tl_oos[k]) for k in range(_K)])
    d = tl_sh - ew_sh
    ddd = tl_rd[:, 1] - ew_rd[:, 1]
    dr = tl_rd[:, 0] - ew_rd[:, 0]
    do = tl_oo - ew_oo

    print("\n" + "=" * 88)
    print(f"REGIMESIGN EV-TILT under INTEGER-LOT — {_K} paired shuffles, ¥{_BUDGET:,}/{_SLOTS}-slot, "
          f"100-sh lots, τ={_TAU}")
    print("=" * 88)
    n = diag["neutral"] or 1
    eff_tau = diag["tilt_lots_sum"] / diag["base_lots_sum"] if diag["base_lots_sum"] else float("nan")
    print(f"\nGRANULARITY DIAGNOSTIC (1 shuffle/FY): neutral fills {diag['neutral']}, "
          f"mean base_lots {diag['base_lots_sum']/n:.2f}")
    print(f"  trim rounds neutral name to ZERO lots: {diag['trim_to_zero']}/{diag['neutral']} "
          f"({diag['trim_to_zero']/n*100:.0f}%)  | realized aggregate trim τ_eff = {eff_tau:.3f} "
          f"(nominal {_TAU})")

    print(f"\n{'arm':<14}{'Sharpe mean':>12}{'sd':>7}{'p5':>7}{'p50':>7}{'p95':>7}"
          f"{'ret mean':>10}{'DD mean':>9}")
    print(f"{'EW-LOT':<14}{ew_sh.mean():>12.3f}{ew_sh.std():>7.3f}"
          f"{np.percentile(ew_sh,5):>7.2f}{np.percentile(ew_sh,50):>7.2f}{np.percentile(ew_sh,95):>7.2f}"
          f"{ew_rd[:,0].mean()*100:>9.0f}%{ew_rd[:,1].mean()*100:>8.1f}%")
    print(f"{'TILT-LOT':<14}{tl_sh.mean():>12.3f}{tl_sh.std():>7.3f}"
          f"{np.percentile(tl_sh,5):>7.2f}{np.percentile(tl_sh,50):>7.2f}{np.percentile(tl_sh,95):>7.2f}"
          f"{tl_rd[:,0].mean()*100:>9.0f}%{tl_rd[:,1].mean()*100:>8.1f}%")

    print(f"\n[paired Δ = TILT-LOT − EW-LOT, same fills each shuffle]")
    print(f"  Δ Sharpe  mean {d.mean():+.3f} | sd {d.std():.3f} | "
          f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}] | "
          f"P(Δ>0) {(d>0).mean():.3f} ({int((d>0).sum())}/{_K})")
    print(f"  Δ maxDD   mean {ddd.mean()*100:+.2f}pp (positive = shallower) | "
          f"P(shallower) {(ddd>0).mean():.3f}")
    print(f"  Δ return  mean {dr.mean()*100:+.1f}pp | P(Δ>0) {(dr>0).mean():.3f}")
    print(f"  OOS FY2025 Δ Sharpe mean {do.mean():+.3f} | P(Δ>0) {(do>0).mean():.3f}")

    print("\n" + "-" * 88)
    passes = (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
    dd_ok = ddd.mean() >= 0
    if passes and dd_ok:
        verdict = ("SURVIVES integer-lot granularity — the neutral trim still beats EW-LOT on the realistic "
                   "¥2M/6-slot book net of fill-order luck. Idealized edge is realizable.")
    elif dd_ok and ddd.mean() > 0.5:
        verdict = ("PARTIAL — Sharpe edge washes under rounding but the maxDD cut survives (the drawdown "
                   "lever is realizable even if the thin Sharpe gain isn't).")
    else:
        verdict = ("DEGRADES — coarse lot rounding (trim→0 on cheap-slot names) erodes the idealized edge; "
                   "the trim is not cleanly realizable at ¥2M granularity.")
    print(f"  VERDICT: {verdict}")
    print(f"  (Gate: P(Δ>0)≥0.95 AND 95% CI-lo>0 AND maxDD not worsened, on the integer-lot book.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
