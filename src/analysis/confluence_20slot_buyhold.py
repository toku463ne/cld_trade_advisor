"""Curiosity benchmark: confluence at ¥10M / 20 slots vs buy-and-hold N225 ETF.

Operator question (2026-05-30): if the budget is ¥10M and we run 20 slots, does
confluence beat just holding the N225 ETF?

Capital-aware fractional book (same model as confluence_buyhold.py): each daily
position return is divided by n_slots; empty slots sit in CASH. ¥10M budget ⇒
¥10M/20 = ¥500k per slot, which dwarfs a 100-share JP lot for nearly every name,
so integer-lot rounding is negligible and the fractional model is a fair proxy
(the dominant effect at 20 slots is CASH DRAG, not lot granularity).

CRUX (breadth diagnostic): confluence surfaces only ~8 low-corr names/day. A
20-slot book (1 high + 19 low) can therefore fill at most ~8 → ~12 slots sit in
cash, so the book is really ~40% confluence + ~60% cash. Reported as mean
concurrent held names / active day + % invested.

For context the script also runs the 6-slot production book and equal-weight
universe BH on the SAME trading days. Deterministic entry-date fill order (like
confluence_buyhold.py); a curiosity benchmark, not a pre-registered null.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_20slot_buyhold
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
# (label, n_slots, _MAX_LOW_CORR=n_slots-1)  — 1 high-corr slot + (n-1) low/mid
_CONFIGS = [("conf6", 6, 5), ("conf20", 20, 19)]
_FYS = [FyConfig("FY2017", datetime.date(2017, 4, 1), datetime.date(2018, 3, 31),
                 "classified2016"),
        FyConfig("FY2018", datetime.date(2018, 4, 1), datetime.date(2019, 3, 31),
                 "classified2017")] + list(RS_FY_CONFIGS)


def _closes(cache: DataCache) -> tuple[list[datetime.date], dict]:
    dts, cmap, seen = [], {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close
    dts.sort()
    return dts, cmap


def _pos_daily(p, dts: list[datetime.date], cmap: dict) -> dict:
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


def _metrics(rets: list[float]) -> tuple[float, float, float]:
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

    stitched = {lab: [] for lab, _, _ in _CONFIGS}
    stitched["n225"] = []
    stitched["univ"] = []
    held_acc = {lab: [] for lab, _, _ in _CONFIGS}   # per-FY mean held/active day
    inv_acc = {lab: [] for lab, _, _ in _CONFIGS}     # per-FY % invested

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
                corr_maps.get(code, {}), zs_maps.get(code, {}),
                cfg.start, cfg.end, _N_GATE))

        n_dts, n_cmap = _closes(n225)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        cal_set = set(cal)
        stock_dts = {code: _closes(c) for code, c in caches.items()}

        for lab, n_slots, max_low in _CONFIGS:
            exsim._MAX_LOW_CORR = max_low
            results = run_simulation(cands, cbt._EXIT_RULE, caches, cfg.end)
            day_contrib = defaultdict(float)
            day_nopen = defaultdict(int)
            for p in results:
                dts, cmap = stock_dts.get(p.stock_code, ([], {}))
                for d, r in _pos_daily(p, dts, cmap).items():
                    if d in cal_set:
                        day_contrib[d] += r / n_slots
                        day_nopen[d] += 1
            conf_r = [day_contrib.get(d, 0.0) for d in cal]
            stitched[lab] += conf_r[1:]
            active = [day_nopen[d] for d in cal if day_nopen.get(d, 0) > 0]
            held_acc[lab].append(float(np.mean(active)) if active else 0.0)
            inv_acc[lab].append(
                statistics.mean([min(day_nopen.get(d, 0), n_slots) / n_slots for d in cal]) if cal else 0.0)
        exsim._MAX_LOW_CORR = 5

        n225_r = [n_cmap[cal[i]] / n_cmap[cal[i - 1]] - 1.0 for i in range(1, len(cal))]
        univ_r = []
        for i in range(1, len(cal)):
            d, dp = cal[i], cal[i - 1]
            rs = [cmap[d] / cmap[dp] - 1.0 for _, (dts, cmap) in stock_dts.items()
                  if d in cmap and dp in cmap and cmap[dp] > 0]
            univ_r.append(statistics.mean(rs) if rs else 0.0)
        stitched["n225"] += n225_r
        stitched["univ"] += univ_r
        logger.info("  {} done", cfg.label)

    print("\n" + "=" * 78)
    print("CONFLUENCE ¥10M / 20-slot vs N225 ETF buy-and-hold — stitched FY2017-2025")
    print("=" * 78)
    print(f"\n{'book':<16}{'total ret':>11}{'Sharpe':>9}{'maxDD':>9}"
          f"{'mean held/day':>15}{'% invested':>12}")
    for lab, n_slots, _ in _CONFIGS:
        t, sh, dd = _metrics(stitched[lab])
        mh = statistics.mean(held_acc[lab])
        inv = statistics.mean(inv_acc[lab])
        name = f"confluence-{n_slots}"
        print(f"{name:<16}{t*100:>+10.1f}%{sh:>+9.2f}{dd*100:>+8.1f}%"
              f"{mh:>11.1f}/{n_slots:<3}{inv*100:>11.0f}%")
    for lab, name in [("n225", "N225 ETF (BH)"), ("univ", "universe EW (BH)")]:
        t, sh, dd = _metrics(stitched[lab])
        print(f"{name:<16}{t*100:>+10.1f}%{sh:>+9.2f}{dd*100:>+8.1f}%{'100% (1 asset)':>27}")

    print("\n(Sharpe = daily ×√252; maxDD on stitched curve; mean held = avg concurrent")
    print(" names on days with >=1 position; % invested = avg fraction of slots filled.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
