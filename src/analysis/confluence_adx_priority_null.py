"""ADX-priority vs its OWN paired fill-order null — the decisive gate test.

The single-arm test (confluence_adx_priority.py) positioned one deterministic
ADX-priority ordering at p92 in the shuffle null (perm p=0.080) — suggestive but
a near-miss, and confounded with order luck. This isolates the ADX effect the way
confluence_capacity_null.py settled the 6-slot question: PAIRED shuffles.

Method: K random within-day fill orders. For each, run TWO books on the SAME
order:
  A (random)      : the shuffled order as-is.
  B (adx-tiebreak): the same order re-sorted within each entry_date by ADX
                    priority (trending=2 > choppy=1 > mid=0, per-FY ADX
                    terciles), preserving the shuffle as the within-bucket
                    tiebreak.
Pairing by seed removes order luck, so Δ = Sharpe(B) − Sharpe(A) is the pure ADX
effect. ADX-priority is real (ship-able as a gate) only if the paired Δ
distribution reliably excludes 0 (P(Δ>0) >= 0.95 and 95% CI lower bound > 0) —
the same bar capacity nearly cleared.

NOTE: ZsTpSl exit does not populate ADX14, so _add_adx() is called on each cache.

OUTCOME (2026-05-23, 200 paired shuffles): REJECT — the decisive test. random(A)
Sharpe mean 0.89; +ADX tiebreak(B) 0.91. Paired Δ Sharpe +0.029, sd 0.217, 95% CI
[−0.396, +0.420], P(Δ>0)=0.545 (109/200) = coin flip; Δ return +18pp, Δ maxDD
−1.1pp. The single-arm 1.15/p92/perm-p0.080 (confluence_adx_priority.py) was ORDER
LUCK — a favorable deterministic ordering, not an ADX effect. Pairing removes the
luck and the ADX tiebreak adds ~nothing. Strongest selector to date still dies at
the portfolio level — same fate as RS/corr-greedy/prefer_b0. Do NOT gate, do NOT
surface in UI. See project_confluence_phase_regime.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_adx_priority_null
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
from src.analysis.exit_benchmark import FyConfig, _add_adx
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 4
_K = 200
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


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


def _metrics(rets):
    if len(rets) < 2:
        return float("nan"), float("nan")
    eq = np.cumprod(1.0 + np.asarray(rets))
    runmax = np.maximum.accumulate(eq)
    return eq[-1] - 1.0, float((eq / runmax - 1.0).min())


def _fy_returns(cands, caches, cfg, stock_dts, cal):
    cal_set = set(cal)
    results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)
    day_contrib = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_contrib[d] += r / _SLOTS
    return [day_contrib.get(d, 0.0) for d in cal]


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
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

    st_A = [[] for _ in range(_K)]   # random order
    st_B = [[] for _ in range(_K)]   # + ADX tiebreak

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
                    _add_adx(c)
                    caches[code] = c
        corr_maps = {code: cbt._build_corr_map(c, n225) for code, c in caches.items()}
        zs_maps = {code: _build_zs_map(c, n225) for code, c in caches.items()}
        n_dts, n_cmap = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}

        stock_adx = {}
        for code, c in caches.items():
            m = {}
            for b in c.bars:
                a = b.indicators.get("ADX14")
                if a and a == a:
                    m[b.dt.date()] = a
            stock_adx[code] = m

        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))

        adx_of = {id(c): stock_adx.get(c.stock_code, {}).get(c.entry_date) for c in cands}
        vals = np.array([a for a in adx_of.values() if a is not None])
        lo, hi = (np.percentile(vals, [33.33, 66.67]) if vals.size else (0.0, 0.0))

        def _prio(c):
            a = adx_of[id(c)]
            if a is None:
                return 0
            if a > hi:
                return 2
            if a <= lo:
                return 1
            return 0

        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)                                   # A: random order
            # B: same order, ADX-priority as a within-day stable tiebreak
            pool_b = sorted(pool, key=lambda c: (c.entry_date, -_prio(c)))
            st_A[k] += _fy_returns(pool, caches, cfg, stock_dts, cal)[1:]
            st_B[k] += _fy_returns(pool_b, caches, cfg, stock_dts, cal)[1:]
        logger.info("  {} done ({} candidates, ADX cuts {:.1f}/{:.1f})",
                    cfg.label, len(cands), lo, hi)

    shA = np.array([_sharpe(st_A[k]) for k in range(_K)])
    shB = np.array([_sharpe(st_B[k]) for k in range(_K)])
    rtA = np.array([_metrics(st_A[k])[0] for k in range(_K)])
    rtB = np.array([_metrics(st_B[k])[0] for k in range(_K)])
    ddA = np.array([_metrics(st_A[k])[1] for k in range(_K)])
    ddB = np.array([_metrics(st_B[k])[1] for k in range(_K)])

    print("\n" + "=" * 80)
    print(f"ADX-PRIORITY PAIRED NULL — {_K} paired shuffles (random vs +ADX tiebreak)")
    print("=" * 80)
    print(f"\n{'config':<16}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for name, s_, r_, d_ in (("random (A)", shA, rtA, ddA),
                             ("+ADX tiebreak (B)", shB, rtB, ddB)):
        print(f"{name:<16}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{r_.mean()*100:>9.0f}%{d_.mean()*100:>8.0f}%")

    d = shB - shA
    print(f"\n[paired Δ Sharpe = B − A, same fill order each draw]")
    print(f"  mean {d.mean():+.3f} | sd {d.std():.3f} | "
          f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}]")
    print(f"  P(Δ > 0) = {(d > 0).mean():.3f}  ({int((d>0).sum())}/{_K} shuffles)")
    dr = rtB - rtA
    print(f"  paired Δ return mean {dr.mean()*100:+.0f}pp | P(Δ>0)={(dr>0).mean():.3f}")
    ddd = ddB - ddA
    print(f"  paired Δ maxDD mean {ddd.mean()*100:+.1f}pp (positive = B shallower DD)")

    cert = (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
    print(f"\n  VERDICT: {'CERTIFIED — ADX tiebreak beats order luck at 95%' if cert else 'NOT separated at 95% — strongest near-miss, park / UI hint only'}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
