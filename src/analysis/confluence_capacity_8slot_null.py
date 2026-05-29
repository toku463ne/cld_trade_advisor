"""6→8 slot capacity vs its own fill-order null, paired shuffles (backlog item 5).

4→6 slots shipped (confluence_capacity_null: 6-slot band above 4-slot, Δ +0.137, P=0.865, adopted on
risk-asymmetry). 6→8 never tested. LOW prior: Stage-0 found only ~8 low-corr names/day → an 8-slot book
(1 high + 7 low) is at the breadth ceiling; the extra slots either sit in cash (capital-aware denom = 8 →
under-investment) or force in correlated names (false diversification). Also raises manual burden (live
plan = 6 slots). Pre-reg docs/analysis/confluence_capacity_8slot_preregistration.md.

Method (identical to the shipped 4→6 null): K=200 paired shuffles, FY2018-2025. Each shuffle seed feeds
the SAME within-day fill order to a 6-slot (_MAX_LOW_CORR=5) and an 8-slot (=7) book, each marked
capital-aware (daily contribution = r/(1+low)). Δ = Sharpe(8) − Sharpe(6) is the capacity effect net of
fill-order noise. BREADTH diagnostic (the crux): mean concurrent held names/active-day + total trades per
arm — if 8-slot mean-held ≈ 6-slot, the extra slots are breadth-starved (cash drag, not diversification).

GATE (frozen): ACCEPT iff P(Δ Sharpe>0) >= 0.95 AND 95% CI-lo > 0. A near-miss is NOT adopted (unlike
4→6: this raises manual burden + low prior, so the reversible-one-liner risk-asymmetry argument does not
apply). REJECT on near-miss / flat-negative / breadth starvation.

OUTCOME (2026-05-29, K=200, FY2018-2025): REJECT — not separated. 8-slot Sharpe 0.946 vs 6-slot 0.911,
Δ +0.035, P(Δ>0)=0.635, CI [−0.152,+0.215] straddles 0; Δ return −10pp; Δ maxDD +1.83pp shallower
(P=0.785); OOS FY2025 Δ +0.216 (P=0.840, leans + but not binding). BREADTH PRIOR REFUTED: the 8-slot
book is NOT starved — mean held 7.92/day (vs 6-slot 5.99), 555 vs 413 trades, so there ARE enough
low-corr names to fill 8 slots. The lever fails instead on DIMINISHING RETURNS to diversification: 6→8
adds only +0.035 Sharpe (vs +0.137 for 4→6) while dragging return −10pp, because the book is already
well-diversified and the marginal later-fill names are lower quality. Unlike 4→6 this raises manual
burden, so the near-miss risk-asymmetry adoption argument does not apply → REJECT, keep 6 slots. This
exhausts the capacity axis (4→6 adopted, 6→8 rejected). See backlog item 5 + the pre-reg.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_capacity_8slot_null
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
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K = 200
_LOWS = [5, 7]   # 6-slot (production) vs 8-slot
# FY2018-2025 (matches the current baseline + items 2/6), = FY2018 + RS_FY_CONFIGS
_FYS = [FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
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


def _metrics(rets):
    if len(rets) < 2:
        return float("nan"), float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    sd = statistics.stdev(rets)
    sh = statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")
    runmax = np.maximum.accumulate(eq)
    return eq[-1] - 1.0, sh, float((eq / runmax - 1.0).min())


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _fy_returns(pool, caches, cfg, stock_dts, cal, n_slots, want_breadth=False):
    cal_set = set(cal)
    results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
    day_contrib = defaultdict(float)
    held = defaultdict(int)   # concurrent held names per date (for breadth diagnostic)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_contrib[d] += r / n_slots
                held[d] += 1
    seq = [day_contrib.get(d, 0.0) for d in cal]
    if want_breadth:
        active = [held[d] for d in cal if held.get(d, 0) > 0]
        return seq, (float(np.mean(active)) if active else 0.0), len(results)
    return seq, None, None


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

    st = {low: [[] for _ in range(_K)] for low in _LOWS}
    oos = {low: [[] for _ in range(_K)] for low in _LOWS}
    breadth = {low: [] for low in _LOWS}    # per-FY mean held (shuffle 0)
    trades = {low: 0 for low in _LOWS}      # total trades (shuffle 0)

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
        n_dts, _ = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)            # SAME order fed to both slot configs
            for low in _LOWS:
                exsim._MAX_LOW_CORR = low
                seq, mh, nt = _fy_returns(pool, caches, cfg, stock_dts, cal, 1 + low,
                                          want_breadth=(k == 0))
                st[low][k] += seq[1:]
                if cfg.label == "FY2025":
                    oos[low][k] += seq[1:]
                if k == 0:
                    breadth[low].append(mh); trades[low] += nt
        exsim._MAX_LOW_CORR = 5
        logger.info("  {} done ({} candidates, {} paired shuffles)", cfg.label, len(cands), _K)

    sh = {low: np.array([_sharpe(st[low][k]) for k in range(_K)]) for low in _LOWS}
    rt = {low: np.array([_metrics(st[low][k])[0] for k in range(_K)]) for low in _LOWS}
    dd = {low: np.array([_metrics(st[low][k])[2] for k in range(_K)]) for low in _LOWS}
    oos_sh = {low: np.array([_sharpe(oos[low][k]) for k in range(_K)]) for low in _LOWS}

    print("\n" + "=" * 82)
    print(f"CAPACITY vs FILL-ORDER NULL — {_K} paired shuffles, 6-slot vs 8-slot, FY2018-2025")
    print("=" * 82)
    print(f"\n{'config':<10}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for low in _LOWS:
        s_ = sh[low]
        print(f"{1+low}-slot{'':<3}{s_.mean():>13.3f}{s_.std():>7.3f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{rt[low].mean()*100:>9.0f}%{dd[low].mean()*100:>8.0f}%")

    print(f"\nBREADTH DIAGNOSTIC (shuffle 0): mean concurrent held names / active day, total trades")
    for low in _LOWS:
        mh = float(np.mean(breadth[low])) if breadth[low] else float("nan")
        print(f"  {1+low}-slot (cap {1+low}): mean held {mh:.2f}  |  total trades {trades[low]}")

    d = sh[7] - sh[5]    # paired Δ Sharpe (8-slot − 6-slot)
    print(f"\n[paired Δ Sharpe = 8-slot − 6-slot, same fill order each draw]")
    print(f"  mean {d.mean():+.3f} | sd {d.std():.3f} | "
          f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}] | "
          f"P(Δ>0) {(d>0).mean():.3f} ({int((d>0).sum())}/{_K})")
    dr = rt[7] - rt[5]
    print(f"  paired Δ return mean {dr.mean()*100:+.0f}pp | P(Δ>0)={(dr>0).mean():.3f}")
    ddd = dd[7] - dd[5]
    print(f"  paired Δ maxDD mean {ddd.mean()*100:+.2f}pp (positive = 8-slot shallower) | "
          f"P(shallower) {(ddd>0).mean():.3f}")
    do = oos_sh[7] - oos_sh[5]
    print(f"  OOS FY2025 Δ Sharpe mean {do.mean():+.3f} | P(Δ>0) {(do>0).mean():.3f}")

    print("\n" + "-" * 82)
    if (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0:
        verdict = "ACCEPT — 8-slot band sits above 6-slot net of fill-order luck"
    else:
        verdict = ("REJECT — 8-slot Δ not cleanly separated (P<0.95 / CI grazes 0); a near-miss is NOT "
                   "adopted here (raises manual burden, low prior, breadth at ceiling). See breadth diag.")
    print(f"  VERDICT: {verdict}")
    print("  (Gate: P(ΔSharpe>0)>=0.95 AND 95% CI-lo>0. Near-miss NOT adopted, unlike 4→6.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
