"""Capacity sweep — CORRECTED. Vary run_simulation's slot cap on the full pool.

v1 (confluence_capacity_sweep.py) was WRONG: it varied slots on run_simulation's
already-4-slot-filtered output. The real slot cap lives INSIDE run_simulation
(_MAX_HIGH_CORR=1, _MAX_LOW_CORR=3). This monkeypatches _MAX_LOW_CORR and feeds
the FULL ~1200/FY candidate pool, so adding slots actually admits more of the
97%-skipped candidates. Capital-aware mark denominator = 1 + _MAX_LOW_CORR.

Arms: low-slot cap 3 (=4 total, production), 4 (=5), 5 (=6), 7 (=8). Baseline
(entry_date) ordering. Same equity model as confluence_buyhold.py.

OUTCOME (2026-05-22): adding slots HELPS (opposite of the buggy v1). Stitched
Sharpe 4-slot +0.84 → 5-slot +0.90 → 6-slot +1.09 → 8-slot +1.03; 6-slot best
(+0.25 ΔSh, +122pp return, maxDD −28.4 vs −29.9), 96% invested throughout (the
full pool fills the extra slots — there are ~6 candidates/day). Intuitive: ≤3
low-corr slots reject most genuinely-diversifying candidates. NEAR-MISS on
certification (confluence_certify.py): 6-slot ΔSharpe CI [−0.03,+0.53] p=0.038
(grazes 0, FAIL), per-FY 5/9 FAIL, BUT FY2025 OOS +0.82 PASS. Most promising
confluence lead — pursue. See project_confluence_xsec_ranking_reject.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_capacity_sweep
"""
from __future__ import annotations

import datetime
import math
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
_LOW_ARMS = [3, 4, 5, 7]   # _MAX_LOW_CORR → total slots 4/5/6/8
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
    total = eq[-1] - 1.0
    sd = statistics.stdev(rets)
    sh = statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")
    runmax = np.maximum.accumulate(eq)
    maxdd = float((eq / runmax - 1.0).min())
    return total, sh, maxdd


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

    stitched = {n: [] for n in _LOW_ARMS}
    per_fy = {n: {} for n in _LOW_ARMS}
    filled = {n: 0 for n in _LOW_ARMS}
    inv = {n: [] for n in _LOW_ARMS}

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
        cands.sort(key=lambda c: c.entry_date)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        cal_set = set(cal)

        for low in _LOW_ARMS:
            exsim._MAX_LOW_CORR = low
            n_slots = 1 + low
            results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)
            filled[low] += len(results)
            day_contrib = defaultdict(float)
            day_nopen = defaultdict(int)
            for p in results:
                sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
                for d, r in _pos_daily(p, sdts, scmap).items():
                    if d in cal_set:
                        day_contrib[d] += r / n_slots
                        day_nopen[d] += 1
            fy_r = [day_contrib.get(d, 0.0) for d in cal]
            inv[low].append(statistics.mean([min(day_nopen.get(d, 0), n_slots) / n_slots
                                             for d in cal]) if cal else 0.0)
            per_fy[low][cfg.label] = _metrics(fy_r)
            stitched[low] += fy_r[1:]
        exsim._MAX_LOW_CORR = 3   # restore
        logger.info("  {} processed ({} raw candidates)", cfg.label, len(cands))

    print("\n" + "=" * 92)
    print("CONFLUENCE CAPACITY SWEEP (CORRECTED) — vary _MAX_LOW_CORR on full pool")
    print("=" * 92)
    sm = {n: _metrics(stitched[n]) for n in _LOW_ARMS}
    print(f"{'slots':>6}{'Sharpe':>9}{'total%':>10}{'maxDD%':>9}{'filled':>8}{'mean inv%':>11}")
    for low in _LOW_ARMS:
        mi = statistics.mean(inv[low]) * 100 if inv[low] else 0.0
        print(f"{1+low:>6}{sm[low][1]:>9.2f}{sm[low][0]*100:>10.1f}{sm[low][2]*100:>9.1f}"
              f"{filled[low]:>8}{mi:>11.0f}")
    base = sm[3][1]
    print("\nΔSharpe vs 4-slot (production):")
    for low in _LOW_ARMS:
        if low == 3:
            continue
        print(f"  {1+low}-slot: {sm[low][1]-base:+.2f}  ({filled[low]-filled[3]:+d} trades)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
