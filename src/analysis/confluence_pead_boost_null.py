"""PEAD-as-score-booster vs its own fill-order null — capital-aware 6-slot book.

The structural fix for the pead_up confluence-VOTE REJECT (confluence_pead_null.py):
the vote ADDED ~20% more candidates that flooded the 6 slots and displaced baseline
trades. This harvest adds ZERO candidates — the price-sign pool is byte-identical to
the baseline — and only REWEIGHTS: a candidate whose stock had an up-revision in the
trailing 60 trading bars is "boosted", and when slot contention exists boosted
candidates fill before non-boosted ones. Confirmation, not initiation.

Pre-registration: docs/analysis/pead_score_boost_preregistration.md (written before
this computation). Test = HARD boosted-first stable sort (the upper bound of any
boost) against the paired fill-order null. Honest prior: this is a selection/ordering
rule, the category with a 100% rejection rate vs this null (ADX-priority paired Δ
+0.029 P=0.545). The only differentiator is the priority key being exogenous +
cross-sectionally validated (+2.51% N225 cohort) rather than price-endogenous. The
bar is unchanged.

Method: K paired shuffles, ONE shared pool (the baseline 10-sign book). For each
seed k, shuffle the pool with Random(k) →
  Arm A (random):       run_simulation(O_k)
  Arm B (boost):        run_simulation(stable-sort O_k boosted-first)
Same realization of fill-order randomness on the same pool, so Δ = Sharpe(B) −
Sharpe(A) isolates the boost-priority effect net of order luck. Capital-aware r/6.

Accept gate 1 (BINDING): P(Δ>0) ≥ 0.95 AND 95% CI lower bound > 0.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_pead_boost_null
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
from src.analysis.confluence_pead_inclusion_ab import (
    _build_pead_up_fires, _load_pead_statements,
)
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_LOW = 5            # production 6-slot book = 1 high + 5 low
_BOOST_LOOKBACK = 60   # trailing trading bars an up-revision keeps a candidate boosted


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


def _metrics(rets):
    if len(rets) < 2:
        return float("nan"), float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    sd = statistics.stdev(rets)
    sh = statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")
    runmax = np.maximum.accumulate(eq)
    return float(eq[-1] - 1.0), sh, float((eq / runmax - 1.0).min())


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _fy_returns(pool, caches, cfg, stock_dts, cal, n_slots):
    cal_set = set(cal)
    results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
    day_contrib: defaultdict[datetime.date, float] = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_contrib[d] += r / n_slots
    return [day_contrib.get(d, 0.0) for d in cal], results


def _build_cands(fires, caches, corr_maps, zs_maps, cfg):
    out = []
    for code in caches:
        out.extend(cbt._candidates_for_stock(
            code, fires.get(code, []), caches[code],
            corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
    return out


def _boosted_set(cands, pead_fires, stock_dts):
    """Set of (stock, entry_date) candidates with an up-revision in trailing 60 bars.

    Look-ahead-safe: the up-revision's entry day is already its after-close-shifted
    tradable day, so any up-revision indexed at/<= the candidate's bar is public.
    """
    boosted: set[tuple[str, datetime.date]] = set()
    for c in cands:
        sdts, _ = stock_dts.get(c.stock_code, ([], {}))
        if not sdts:
            continue
        try:
            di = sdts.index(c.entry_date)
        except ValueError:
            continue
        lo = di - _BOOST_LOOKBACK
        for _sign, ud in pead_fires.get(c.stock_code, []):
            ui = sdts.index(ud) if ud in sdts else None
            if ui is not None and lo <= ui <= di:
                boosted.add((c.stock_code, c.entry_date))
                break
    return boosted


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0   # include early-FY runs (id 9-46); default 47 drops FY2017-19
    cbt._VALID_BARS = dict(_BULLISH)
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.id >= cbt._MULTIYEAR_MIN_RUN_ID,
                   SignBenchmarkRun.sign_type.in_(list(_BULLISH)))).all()
    base_fires: defaultdict[str, list] = defaultdict(list)
    for sg, stk, fa in rows:
        base_fires[stk].append((sg, fa.date() if hasattr(fa, "date") else fa))
    stmts_by_yf = _load_pead_statements()

    fys = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                    "classified2016"),
           FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                    "classified2017")] + list(RS_FY_CONFIGS)

    st = {"A": [[] for _ in range(_K)], "B": [[] for _ in range(_K)]}   # stitched daily
    fy_sh: dict[tuple[str, str], np.ndarray] = {}
    n_filled = {"A": 0, "B": 0}            # gate-5 structural invariant (seed-0 representative)
    boost_r: list[float] = []              # gate-4 mechanism: boosted filled-trade returns
    nonboost_r: list[float] = []

    exsim._MAX_LOW_CORR = _LOW
    for cfg in fys:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 180)
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
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        pead_fires = _build_pead_up_fires(caches, stmts_by_yf)

        cands = _build_cands(base_fires, caches, corr_maps, zs_maps, cfg)   # SHARED pool
        boosted = _boosted_set(cands, pead_fires, stock_dts)

        rets_a: list[list[float]] = []
        rets_b: list[list[float]] = []
        for k in range(_K):
            shuf = cands[:]; random.Random(k).shuffle(shuf)
            ra, res_a = _fy_returns(shuf, caches, cfg, stock_dts, cal, 1 + _LOW)
            # arm B: boosted-first stable sort on the SAME shuffled order
            sb = sorted(shuf, key=lambda c: (c.stock_code, c.entry_date) not in boosted)
            rb, res_b = _fy_returns(sb, caches, cfg, stock_dts, cal, 1 + _LOW)
            rets_a.append(ra); rets_b.append(rb)
            st["A"][k] += ra[1:]
            st["B"][k] += rb[1:]
            if k == 0:                          # seed-0 representative for gates 4 & 5
                n_filled["A"] += len(res_a)
                n_filled["B"] += len(res_b)
                for p in res_b:
                    r = p.exit_price / p.entry_price - 1.0
                    if (p.stock_code, p.entry_date) in boosted:
                        boost_r.append(r)
                    else:
                        nonboost_r.append(r)
        fy_sh[(cfg.label, "A")] = np.array([_sharpe(r) for r in rets_a])
        fy_sh[(cfg.label, "B")] = np.array([_sharpe(r) for r in rets_b])
        logger.info("  {} done ({} cands, {} boosted, {} pead fires, {} shuffles)",
                    cfg.label, len(cands), len(boosted),
                    sum(len(v) for v in pead_fires.values()), _K)
    exsim._MAX_LOW_CORR = 5

    sh = {a: np.array([_sharpe(st[a][k]) for k in range(_K)]) for a in ("A", "B")}
    rt = {a: np.array([_metrics(st[a][k])[0] for k in range(_K)]) for a in ("A", "B")}
    dd = {a: np.array([_metrics(st[a][k])[2] for k in range(_K)]) for a in ("A", "B")}

    print("\n" + "=" * 86)
    print(f"PEAD SCORE-BOOST (hard boosted-first) vs FILL-ORDER NULL — {_K} paired shuffles, 6-slot")
    print("=" * 86)
    print(f"\n{'arm':<16}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for a, name in [("A", "random"), ("B", "boost-first")]:
        s_ = sh[a]
        print(f"{name:<16}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{rt[a].mean()*100:>9.0f}%{dd[a].mean()*100:>8.0f}%")

    d = sh["B"] - sh["A"]    # paired Δ Sharpe (boost − random), same fill order per seed
    p_pos = float((d > 0).mean())
    ci_lo, ci_hi = float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))
    print("\n[paired Δ Sharpe = boost-first − random, same fill order each draw]")
    print(f"  mean {d.mean():+.3f} | sd {d.std():.3f} | 95% CI [{ci_lo:+.3f}, {ci_hi:+.3f}]")
    print(f"  P(Δ > 0) = {p_pos:.3f}  ({int((d>0).sum())}/{_K} shuffles)")
    dr = rt["B"] - rt["A"]
    print(f"  paired Δ return mean {dr.mean()*100:+.0f}pp | P(Δ>0)={(dr>0).mean():.3f}")
    ddd = dd["B"] - dd["A"]
    print(f"  paired Δ maxDD mean {ddd.mean()*100:+.1f}pp (positive = boost shallower DD)")

    # GATE 2 — whole-band shift, not a fat tail
    g2 = (np.percentile(sh["B"], 5) >= np.percentile(sh["A"], 5)
          and np.percentile(sh["B"], 50) >= np.percentile(sh["A"], 50))
    print(f"\n  GATE 2 (band shift p5 & p50): {'PASS' if g2 else 'FAIL'} "
          f"[B p5 {np.percentile(sh['B'],5):.2f} vs A {np.percentile(sh['A'],5):.2f}; "
          f"B p50 {np.percentile(sh['B'],50):.2f} vs A {np.percentile(sh['A'],50):.2f}]")

    # GATE 3 — per-FY robustness
    print("\nPER-FY paired Δ Sharpe (boost-first − random):")
    pos_fy, oos_ok = 0, None
    n_fy = 0
    for cfg in fys:
        ka, kb = (cfg.label, "A"), (cfg.label, "B")
        if ka not in fy_sh:
            continue
        n_fy += 1
        dfy = fy_sh[kb] - fy_sh[ka]
        dfy = dfy[~np.isnan(dfy)]
        if len(dfy) == 0:
            continue
        mean_dfy = float(dfy.mean())
        if mean_dfy > 0:
            pos_fy += 1
        if cfg.label == "FY2025":
            oos_ok = mean_dfy > 0
        tag = "  ← OOS" if cfg.label == "FY2025" else ""
        print(f"  {cfg.label}  Δ mean {mean_dfy:+.3f} | P(Δ>0)={(dfy>0).mean():.2f}{tag}")
    g3 = pos_fy >= 6 and bool(oos_ok)
    print(f"  GATE 3 (≥6/{n_fy} FYs Δ>0 AND OOS FY2025 Δ>0): {'PASS' if g3 else 'FAIL'} "
          f"[{pos_fy}/{n_fy} positive; OOS {'pos' if oos_ok else 'neg/NA'}]")

    # GATE 4 — boosted filled trades genuinely better (seed-0 representative)
    bm = statistics.mean(boost_r) if boost_r else float("nan")
    nm = statistics.mean(nonboost_r) if nonboost_r else float("nan")
    bw = (sum(1 for r in boost_r if r > 0) / len(boost_r)) if boost_r else float("nan")
    nw = (sum(1 for r in nonboost_r if r > 0) / len(nonboost_r)) if nonboost_r else float("nan")
    g4 = (not math.isnan(bm)) and bm > nm and bw > nw
    print(f"\n  GATE 4 (boosted trades better, seed-0): "
          f"boosted n={len(boost_r)} mean_r {bm*100:+.2f}% win {bw*100:.0f}% | "
          f"non-boosted n={len(nonboost_r)} mean_r {nm*100:+.2f}% win {nw*100:.0f}% "
          f"→ {'PASS' if g4 else 'FAIL'}")

    # GATE 5 — structural invariant (filled-trade count ≈ equal, seed-0 over all FYs)
    g5 = abs(n_filled["A"] - n_filled["B"]) <= max(2, round(0.03 * n_filled["A"]))
    print(f"  GATE 5 (filled-trade count ≈ equal, seed-0): A={n_filled['A']} B={n_filled['B']} "
          f"→ {'PASS' if g5 else 'FAIL (candidate leak?)'}")

    certified = p_pos >= 0.95 and ci_lo > 0
    print("\n" + "-" * 86)
    print(f"  GATE 1 (BINDING null): P(Δ>0)={p_pos:.3f} (need ≥0.95), CI lower {ci_lo:+.3f} "
          f"(need >0) → {'PASS' if certified else 'FAIL'}")
    if certified and g2 and g3 and g4 and g5:
        verdict = "SHIP — boost-first clears the binding null with band-shift + per-FY + mechanism"
    elif certified:
        verdict = "MIXED — binding null clears but a robustness gate failed → treat as not-ship"
    else:
        verdict = ("REJECT — boost-first within fill-order noise; same fate as ADX-priority "
                   "and every price-endogenous selection rule. Exogenous key did not change the bar.")
    print(f"  VERDICT: {verdict}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
