"""Capacity test: does adding slots beat the 4-slot book?

Stage-0 (confluence_selection_pressure.py) found the binding constraint isn't
name-choice (RS re-rank REJECTED) but CAPACITY: the book sits at 3.58/4 and 16%
of valid confluence proposals expire unfilled for lack of a slot. This sweeps
the slot count to see whether unlocking those expired proposals improves the
capital-aware equity curve — or just adds cash drag (with ~3.7 expected
concurrent names, more slots can sit idle).

Each arm: N equal slots (1/N capital each), ≤1 high-corr seat regardless of N,
FIFO fill (RS ranking already rejected), K=7d proposal shelf-life. Same
capital-aware daily-marked equity model as confluence_buyhold.py. Reports
per-FY + stitched total/Sharpe/maxDD, taken-trade count, and mean invested %.

OUTCOME (2026-05-22): REJECT. Adding slots HURTS — stitched Sharpe 4-slot +0.95
> 5-slot +0.84 > 6/8-slot +0.85; total return collapses 276%→96%. Capacity does
unlock trades (275→317, confirming the 16% expiry) but they're marginal FIFO
leftovers that dilute, and with ~3.7 expected concurrent names the extra slots
sit in CASH (mean invested 81%→48%) — cash drag kills return. 6-slot and 8-slot
take the IDENTICAL 317 (not enough concurrent candidates beyond 6). maxDD
"improves" only via less exposure. The 4-slot book is already right-sized for
the candidate flow; the only real lever is a bigger UNIVERSE (more trades/yr →
more concurrent names), not more slots. See project_confluence_xsec_ranking_reject.md.

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
from src.analysis.exit_benchmark import FyConfig
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import RS_FY_CONFIGS, _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache

_BULLISH = {"str_hold": 3, "str_lead": 5, "str_lag": 5, "brk_sma": 5, "brk_bol": 3,
            "rev_lo": 5, "rev_nlo": 5, "brk_kumo_hi": 5, "brk_tenkan_hi": 5, "chiko_hi": 5}
_N_GATE = 3
_K_DAYS = 7
_SLOT_ARMS = [4, 5, 6, 8]
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


def _pos_daily(entry_date, exit_date, entry_price, exit_price, dts, cmap):
    try:
        ie, ix = dts.index(entry_date), dts.index(exit_date)
    except ValueError:
        return {}
    out = {}
    if ie == ix:
        out[entry_date] = exit_price / entry_price - 1.0
        return out
    span = dts[ie:ix + 1]
    for k, d in enumerate(span):
        if k == 0:
            out[d] = cmap[d] / entry_price - 1.0
        elif d == exit_date:
            out[d] = exit_price / cmap[span[k - 1]] - 1.0
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


def _select(trades, n_slots):
    """FIFO N-slot fill, ≤1 high-corr seat. Returns (taken ids, by_id)."""
    cand = sorted(trades, key=lambda t: t["entry_date"])
    for i, t in enumerate(cand):
        t["_id"] = i
    live = []
    ci = 0
    free_after = [datetime.date.min] * n_slots
    high_busy_until = datetime.date.min
    taken = set()
    evt_days = sorted({t["entry_date"] for t in trades} | {t["exit_date"] for t in trades})
    for d in evt_days:
        while ci < len(cand) and cand[ci]["entry_date"] <= d:
            t = cand[ci]
            live.append((t["entry_date"] + datetime.timedelta(days=_K_DAYS), t))
            ci += 1
        live = [(exp, t) for (exp, t) in live if exp >= d]
        free_idx = [i for i, fa in enumerate(free_after) if fa < d]
        hi_free = high_busy_until < d
        while free_idx:
            elig = [(exp, t) for (exp, t) in live if not (t["corr"] == "high" and not hi_free)]
            if not elig:
                break
            pick = min(elig, key=lambda et: et[1]["entry_date"])
            live.remove(pick)
            t = pick[1]
            i = free_idx.pop(0)
            free_after[i] = t["exit_date"]
            if t["corr"] == "high":
                high_busy_until = t["exit_date"]
                hi_free = False
            taken.add(t["_id"])
    return taken, {t["_id"]: t for t in cand}


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

    stitched = {n: [] for n in _SLOT_ARMS}
    per_fy = {n: {} for n in _SLOT_ARMS}
    taken_ct = {n: 0 for n in _SLOT_ARMS}
    inv_samples = {n: [] for n in _SLOT_ARMS}
    n_total = 0

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
        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))
        results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)
        n_total += len(results)

        n_dts, n_cmap = _closes(n225)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        trades = [{
            "entry_date": p.entry_date, "exit_date": p.exit_date,
            "entry_price": p.entry_price, "exit_price": p.exit_price,
            "corr": p.corr_mode, "stock": p.stock_code,
        } for p in results]

        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        cal_set = set(cal)

        for n in _SLOT_ARMS:
            taken, by_id = _select([dict(t) for t in trades], n)
            taken_ct[n] += len(taken)
            day_contrib = defaultdict(float)
            day_nopen = defaultdict(int)
            for tid in taken:
                t = by_id[tid]
                sdts, scmap = stock_dts.get(t["stock"], ([], {}))
                for d, r in _pos_daily(t["entry_date"], t["exit_date"],
                                       t["entry_price"], t["exit_price"], sdts, scmap).items():
                    if d in cal_set:
                        day_contrib[d] += r / n
                        day_nopen[d] += 1
            fy_r = [day_contrib.get(d, 0.0) for d in cal]
            inv = statistics.mean([min(day_nopen.get(d, 0), n) / n for d in cal]) if cal else 0.0
            inv_samples[n].append(inv)
            per_fy[n][cfg.label] = _metrics(fy_r)
            stitched[n] += fy_r[1:]
        logger.info("  {} processed ({} candidates)", cfg.label, len(results))

    # ── report ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 92)
    print(f"CONFLUENCE CAPACITY SWEEP — FIFO N-slot capital-aware book, "
          f"{n_total} candidates, ≤1 high-corr")
    print("=" * 92)
    hdr = f"{'FY':<8}"
    for n in _SLOT_ARMS:
        hdr += f" | {'%d-slot tot/Sh/DD' % n:>20}"
    print(hdr)
    def _fmt(m):
        t, s, dd = m
        return f"{t*100:+6.1f}/{s:+5.2f}/{dd*100:6.1f}"
    for cfg in _FYS:
        if cfg.label not in per_fy[_SLOT_ARMS[0]]:
            continue
        line = f"{cfg.label:<8}"
        for n in _SLOT_ARMS:
            line += f" | {_fmt(per_fy[n][cfg.label]):>20}"
        print(line)
    print("-" * 92)
    sm = {n: _metrics(stitched[n]) for n in _SLOT_ARMS}
    line = f"{'STITCH':<8}"
    for n in _SLOT_ARMS:
        line += f" | {_fmt(sm[n]):>20}"
    print(line)
    print("\n(tot=total return %, Sh=daily Sharpe ×√252, DD=max drawdown %)\n")
    print(f"{'slots':>6}{'Sharpe':>9}{'total%':>10}{'maxDD%':>9}{'taken':>7}{'mean inv%':>11}")
    for n in _SLOT_ARMS:
        mi = statistics.mean(inv_samples[n]) * 100 if inv_samples[n] else 0.0
        print(f"{n:>6}{sm[n][1]:>9.2f}{sm[n][0]*100:>10.1f}{sm[n][2]*100:>9.1f}"
              f"{taken_ct[n]:>7}{mi:>11.0f}")
    base = sm[4][1]
    print("\nΔSharpe vs 4-slot:")
    for n in _SLOT_ARMS:
        if n == 4:
            continue
        print(f"  {n}-slot: {sm[n][1]-base:+.2f}  ({taken_ct[n]-taken_ct[4]:+d} trades)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
