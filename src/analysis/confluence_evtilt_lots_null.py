"""EV-tilt under INTEGER-LOT granularity — the live-readiness check for backlog item 2.

confluence_evtilt_null + confluence_evtilt_phase_null established the neutral-momentum sizing trim
(τ=0.5) on the IDEALIZED equal-weight book (each held name weighted a fractional 1/6). The live book is
NOT fractional: it trades 単元株 (100-share lots) at ¥2,000,000 / 6 slots ≈ ¥333k per slot, so a name's
weight is `position_weight(recommended_lots(...), price)` — whole lots, with cash drag, and names priced
> ~¥3,333 are unaffordable and skip the slot (`recommended_lots == 0`). See src/portfolio/sizing.py and
the budget-book path in confluence_benchmark.py.

GRANULARITY RISK: "trim neutral to τ=0.5" realistically means buy `floor(0.5 × base_lots)` lots on a
neutral-momentum signal. At ~¥333k/slot many names have base_lots = 1 or 2, so floor(0.5×1)=0 → the trim
ROUNDS A 1-LOT NEUTRAL NAME TO ZERO (hold cash in that slot for the hold) and floor(0.5×2)=1 (a true
half). So the realized trim is coarse and biased toward "skip" for cheap-slot names. This probe asks
whether the idealized Δ Sharpe (+0.12) survives that rounding.

METHOD: realistic budget book (mirrors confluence_benchmark.py's bw path). Pre-filter candidates to
AFFORDABLE (recommended_lots>0) so the slots are filled by affordable names only; per shuffle run once
(6-slot). Both arms weight each position by DEPLOYED capital, constant over the hold:
  - EW-LOT  : w_p = position_weight(base_lots, entry_price, BUDGET).
  - TILT-LOT: neutral-entry names w_p = position_weight(floor(τ·base_lots), ...); others base_lots.
Paired fill-order null (same fills per shuffle) on stitched Sharpe + maxDD, FY2018–2025.

Gate (same as the idealized probe): P(Δ Sharpe > 0) ≥ 0.95 AND 95% CI-lo > 0, maxDD not worsened. A
diagnostic reports how often the trim rounds to 0 (the granularity bite) and the realized aggregate trim
vs the nominal τ=0.5.

OUTCOME (2026-05-29, K=200, FY2018–2025): SURVIVES. Granularity bite is real — the τ=0.5 trim rounds
50% of neutral names (59/117) to ZERO lots (mean base_lots only 3.15 at ¥333k/slot), so the realized
aggregate trim is τ_eff=0.394 (harder than nominal); fine, because the idealized τ-curve favored deeper
trims (τ=0.25→+0.173). EW-LOT Sharpe 0.916/maxDD −21.1% (matches benchmark.md baseline −21.8%) →
TILT-LOT 1.042/−17.0%. Paired Δ Sharpe +0.126, P(Δ>0)=0.980, 95% CI [+0.005,+0.252] (CI-lo grazes 0,
consistent with the +0.037/+0.007/+0.005 thinning across fill-order/phase/lot nulls); Δ maxDD +4.13pp
shallower in 99.5% of shuffles; Δ return +0.5pp (FLAT — pure risk improvement, no return cost on the
real book). OOS FY2025 Δ −0.114 (worse than idealized −0.024: in a bull year the aggressive trim cashes
working neutral names = the insurance premium; it pays in FY2021/FY2022). NET: a robust ~4pp DRAWDOWN
cut + thin Sharpe tailwind, realizable at ¥2M integer-lot granularity. → live sizing GUIDELINE (buy
~half lots, rounded down, on a neutral-N225-60bar-momentum entry); operator/sign-debate to adopt; the
live book is manual so there is no exit_simulator constant to flip. See backlog item 2.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_evtilt_lots_null
"""
from __future__ import annotations

import datetime
import math
import random
import statistics
import sys
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.portfolio.sizing import position_weight, recommended_lots
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_SLOTS = 6
_BUDGET = 2_000_000          # real target account (matches confluence_benchmark.py)
_N225_MOM = 60
_T1, _T2 = -0.001, 0.081     # FROZEN tercile cutoffs (same as confluence_evtilt_null)
_TAU = 0.5                   # FROZEN neutral trim factor
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)


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
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))

    ew_st = [[] for _ in range(_K)]      # stitched EW-LOT per shuffle
    tl_st = [[] for _ in range(_K)]      # stitched TILT-LOT per shuffle
    ew_oos = [[] for _ in range(_K)]
    tl_oos = [[] for _ in range(_K)]
    # granularity diagnostic (1 shuffle/FY)
    diag = {"neutral": 0, "trim_to_zero": 0, "base_lots_sum": 0, "tilt_lots_sum": 0}

    exsim._MAX_LOW_CORR = 5
    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE)
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
        n_dts, n_cmap = _closes(n225)
        n_idx = {d: i for i, d in enumerate(n_dts)}
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        # affordability pre-filter (mirrors confluence_benchmark.py bw path): a name whose
        # one lot can't fit a budget/_SLOTS slot can't be held → must not consume a slot.
        def _affordable(c) -> bool:
            _, cmap = stock_dts.get(c.stock_code, ([], {}))
            px = cmap.get(c.entry_date)
            return px is not None and recommended_lots(_BUDGET, float(px), _SLOTS) > 0
        cands = [c for c in cands if _affordable(c)]
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        regime_of = {(c.stock_code, c.entry_date):
                     _regime_at(n_dts, n_idx, n_cmap, c.entry_date) for c in cands}

        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)
            results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
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
    print(f"EV-TILT under INTEGER-LOT granularity — {_K} paired shuffles, ¥{_BUDGET:,}/{_SLOTS}-slot, "
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
