"""RS re-rank A/B — CORRECTED. Order the FULL candidate pool, not the output.

v1 (confluence_rs_rerank.py) was WRONG: it fed run_simulation's already-4-slot-
filtered output (~34/FY) into a second 4-slot selector. run_simulation ALREADY
enforces ≤1 high + ≤3 low slots and SKIPS (not queues) candidates when slots are
full — so ~1474 candidates/FY compete for 4 slots and ~97% are skipped. The real
selection happens INSIDE run_simulation, in candidate order.

run_simulation does `sorted(candidates, key=entry_date)` — a STABLE sort. So
pre-ordering the candidate list by RS within each day makes run_simulation fill
free slots with the higher-RS names first. That is the correct ranker injection.

Arms (each = the full 1474/FY pool ordered differently, then run_simulation):
  baseline — entry_date only (current arbitrary within-day order)
  rs_high  — (entry_date, −RS): strongest relative strength first
  rs_low   — (entry_date, +RS): weakest first (falsification)
RS = trailing-60-bar stock return − N225 return as of entry_date.
Equity curve = capital-aware 4-slot mark (same as confluence_buyhold.py).

OUTCOME (2026-05-22): point estimate MONOTONE-positive (baseline +0.84 <
RS-high +0.93 < falsification confirms; RS-low +0.70) on 10,774 raw candidates,
but REJECT on certification (confluence_certify.py): block-bootstrap ΔSharpe CI
[−0.34,+0.59] p=0.31 FAIL, per-FY 5/9 FAIL, FY2025 OOS −0.30 FAIL. RS-high
rescues weak years (FY2018/19/21) but wrecks FY2024. So relative-strength
ranking carries no ROBUST selection edge — but NOT because the pool is thin
(it's ~1200/FY); the earlier "n-thin / median-choice-1" claim was a bug (v1 fed
run_simulation's already-4-slot output into a 2nd selector). See
project_confluence_xsec_ranking_reject.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_rs_rerank
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
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_SLOTS = 4
_RS_LOOKBACK = 60
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


def _rs(stock_dts, stock_cmap, n_dts, n_cmap, d):
    def _ret(dts, cmap):
        if d not in cmap:
            return None
        i = dts.index(d)
        if i < _RS_LOOKBACK:
            return None
        p0 = cmap[dts[i - _RS_LOOKBACK]]
        return cmap[d] / p0 - 1.0 if p0 else None
    sr = _ret(stock_dts, stock_cmap)
    if sr is None:
        return 0.0
    return sr - (_ret(n_dts, n_cmap) or 0.0)


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

    arms = ["baseline", "rs_high", "rs_low"]
    stitched = {a: [] for a in arms}
    per_fy = {a: {} for a in arms}
    raw_total = filled = {a: 0 for a in arms}
    filled = {a: 0 for a in arms}
    raw_total = 0

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
        raw_total += len(cands)
        # attach RS
        rs_of = {}
        for c in cands:
            sdts, scmap = stock_dts.get(c.stock_code, ([], {}))
            rs_of[id(c)] = _rs(sdts, scmap, n_dts, n_cmap, c.entry_date)

        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        cal_set = set(cal)

        orderings = {
            "baseline": sorted(cands, key=lambda c: c.entry_date),
            "rs_high":  sorted(cands, key=lambda c: (c.entry_date, -rs_of[id(c)])),
            "rs_low":   sorted(cands, key=lambda c: (c.entry_date,  rs_of[id(c)])),
        }
        for a in arms:
            results = run_simulation(orderings[a], cbt._EXIT_RULE, caches, cfg.end)
            filled[a] += len(results)
            day_contrib = defaultdict(float)
            for p in results:
                sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
                for d, r in _pos_daily(p, sdts, scmap).items():
                    if d in cal_set:
                        day_contrib[d] += r / _SLOTS
            fy_r = [day_contrib.get(d, 0.0) for d in cal]
            per_fy[a][cfg.label] = _metrics(fy_r)
            stitched[a] += fy_r[1:]
        logger.info("  {} processed ({} raw candidates)", cfg.label, len(cands))

    print("\n" + "=" * 88)
    print(f"RS RE-RANK A/B (CORRECTED) — order full pool then run_simulation; "
          f"{raw_total} raw candidates")
    print("=" * 88)
    print(f"{'FY':<8} | {'baseline tot/Sh/DD':>22} | {'RS-high tot/Sh/DD':>22} | "
          f"{'RS-low tot/Sh/DD':>22}")
    def _fmt(m):
        t, s, dd = m
        return f"{t*100:+6.1f}/{s:+5.2f}/{dd*100:6.1f}"
    for cfg in _FYS:
        if cfg.label not in per_fy["baseline"]:
            continue
        print(f"{cfg.label:<8} | {_fmt(per_fy['baseline'][cfg.label]):>22} | "
              f"{_fmt(per_fy['rs_high'][cfg.label]):>22} | "
              f"{_fmt(per_fy['rs_low'][cfg.label]):>22}")
    print("-" * 88)
    sm = {a: _metrics(stitched[a]) for a in arms}
    print(f"{'STITCH':<8} | {_fmt(sm['baseline']):>22} | {_fmt(sm['rs_high']):>22} | "
          f"{_fmt(sm['rs_low']):>22}")
    print(f"\nfilled trades: baseline {filled['baseline']}, rs_high {filled['rs_high']}, "
          f"rs_low {filled['rs_low']}  (from {raw_total} raw)")
    dh = sm["rs_high"][1] - sm["baseline"][1]
    dl = sm["rs_low"][1] - sm["baseline"][1]
    print(f"Stitched Sharpe: baseline {sm['baseline'][1]:+.2f} | "
          f"RS-high {sm['rs_high'][1]:+.2f} (Δ {dh:+.2f}) | "
          f"RS-low {sm['rs_low'][1]:+.2f} (Δ {dl:+.2f})")
    wins = sum(1 for c in _FYS if c.label in per_fy["baseline"]
               and per_fy["rs_high"][c.label][1] > per_fy["baseline"][c.label][1])
    testable = sum(1 for c in _FYS if c.label in per_fy["baseline"])
    print(f"RS-high beats baseline on Sharpe in {wins}/{testable} FYs")
    mono = sm["rs_high"][1] >= sm["baseline"][1] >= sm["rs_low"][1]
    print(f"MONOTONE (RS-high ≥ baseline ≥ RS-low)? {mono}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
