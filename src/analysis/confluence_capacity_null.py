"""Capacity vs its own fill-order null: 6-slot vs 4-slot, paired shuffles.

The 6-slot capacity lead (confluence_capacity_sweep, +0.25 Sharpe vs 4-slot) was
only positioned loosely against the 4-slot shuffle null. Capacity is STRUCTURAL,
not selection, so it can certify at current n IF the 6-slot fill-order
distribution sits above the 4-slot one.

Method: K shuffles. For each shuffle seed, feed the SAME within-day fill order to
both a 4-slot (_MAX_LOW_CORR=3) and a 6-slot (=5) book, mark each capital-aware
(denominator = 1 + low), and record stitched Sharpe/return/maxDD. Pairing by seed
removes order-luck, so Δ = Sharpe(6) − Sharpe(4) is the capacity effect net of
fill-order noise. Capacity is real if the Δ distribution reliably excludes 0.

OUTCOME (2026-05-23, 200 paired shuffles): NEAR-MISS — best confluence lead of
the session. 6-slot's whole distribution shifts up AND tightens vs 4-slot:
Sharpe mean 1.02 vs 0.89, p50 1.04 vs 0.88, p5 0.72 vs 0.60, sd 0.17 vs 0.19;
return 374% vs 335%; maxDD −24% vs −27%. Paired Δ Sharpe mean +0.137, P(Δ>0)
=0.865 (173/200), but 95% CI [−0.095, +0.370] grazes 0 → NOT separated at 95%.
Unlike the selection rules (RS=random, corr-greedy p73, prefer_b0 ≈p89), capacity
moves the whole band with a real mechanism (more low-corr names → lower variance)
and improves DD. Favorable risk asymmetry (one-line _MAX_LOW_CORR=3→5, reversible,
better DD even if the Sharpe gain is noise) → lean-yes/operator-call, gated on
willingness to run a 6-position book. See project_confluence_fill_order_null.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_capacity_null
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
_LOWS = [3, 5]   # 4-slot (production) vs 6-slot
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


def _fy_returns(pool, caches, cfg, stock_dts, cal, n_slots):
    cal_set = set(cal)
    results = run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end)
    day_contrib = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_contrib[d] += r / n_slots
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

    # stitched daily series per (low, shuffle k)
    st = {low: [[] for _ in range(_K)] for low in _LOWS}

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
                st[low][k] += _fy_returns(pool, caches, cfg, stock_dts, cal, 1 + low)[1:]
        exsim._MAX_LOW_CORR = 3
        logger.info("  {} done ({} candidates, {} paired shuffles)", cfg.label, len(cands), _K)

    sh = {low: np.array([_sharpe(st[low][k]) for k in range(_K)]) for low in _LOWS}
    rt = {low: np.array([_metrics(st[low][k])[0] for k in range(_K)]) for low in _LOWS}
    dd = {low: np.array([_metrics(st[low][k])[2] for k in range(_K)]) for low in _LOWS}

    print("\n" + "=" * 80)
    print(f"CAPACITY vs FILL-ORDER NULL — {_K} paired shuffles, 4-slot vs 6-slot")
    print("=" * 80)
    print(f"\n{'config':<10}{'Sharpe mean':>13}{'sd':>7}{'p5':>8}{'p50':>8}{'p95':>8}"
          f"{'ret mean':>10}{'DD mean':>9}")
    for low in _LOWS:
        s_ = sh[low]
        print(f"{1+low}-slot{'':<3}{s_.mean():>13.2f}{s_.std():>7.2f}"
              f"{np.percentile(s_,5):>8.2f}{np.percentile(s_,50):>8.2f}{np.percentile(s_,95):>8.2f}"
              f"{rt[low].mean()*100:>9.0f}%{dd[low].mean()*100:>8.0f}%")

    d = sh[5] - sh[3]    # paired Δ Sharpe (6-slot − 4-slot)
    print(f"\n[paired Δ Sharpe = 6-slot − 4-slot, same fill order each draw]")
    print(f"  mean {d.mean():+.3f} | sd {d.std():.3f} | "
          f"95% CI [{np.percentile(d,2.5):+.3f}, {np.percentile(d,97.5):+.3f}]")
    print(f"  P(Δ > 0) = {(d > 0).mean():.3f}  ({int((d>0).sum())}/{_K} shuffles)")
    dr = rt[5] - rt[3]
    print(f"  paired Δ return mean {dr.mean()*100:+.0f}pp | P(Δ>0)={ (dr>0).mean():.3f}")
    ddd = dd[5] - dd[3]
    print(f"  paired Δ maxDD mean {ddd.mean()*100:+.1f}pp (negative = 6-slot deeper DD)")

    sep = (sh[5].mean() - sh[3].mean()) / math.sqrt(sh[5].std()**2 + sh[3].std()**2)
    print(f"\n  distribution separation (Δmean / pooled sd) = {sep:.2f}")
    verdict = ("REAL — 6-slot band sits above 4-slot net of fill-order luck"
               if (d > 0).mean() >= 0.95 and np.percentile(d, 2.5) > 0
               else "NOT separated — capacity gain is within fill-order noise")
    print(f"  VERDICT: {verdict}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
