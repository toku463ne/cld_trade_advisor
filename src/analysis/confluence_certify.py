"""Bootstrap-certify the two corrected wins vs the production 4-slot book.

Candidates: (A) RS-high ordering @4-slot (ΔSharpe +0.09); (B) 6-slot capacity
@baseline ordering (ΔSharpe +0.25). Both on the full ~1200/FY candidate pool
through run_simulation (the correct level).

Gates (project standard):
  G1 daily block-bootstrap ΔSharpe CI excludes 0  (block=21d, 5000 iters)
  G2 per-FY: variant beats production in >=6/9 FYs
  G3 FY2025 OOS positive ΔSharpe

OUTCOME (2026-05-22): RS-high REJECT (G1 CI [−0.34,+0.59] p=0.31, G2 5/9, G3
−0.30 — all FAIL). 6-slot capacity NEAR-MISS (G1 CI [−0.03,+0.53] p=0.038 grazes
0 FAIL, G2 5/9 FAIL, G3 OOS +0.82 PASS). Neither certified for ship; 6-slot is
the promising lead. See project_confluence_xsec_ranking_reject.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_certify
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


def _sharpe(rets):
    if len(rets) < 2:
        return float("nan")
    sd = statistics.stdev(rets)
    return statistics.mean(rets) / sd * math.sqrt(252) if sd > 0 else float("nan")


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


def _series(cands, caches, stock_dts, n_dts, n_cmap, cal, n_slots, end):
    cal_set = set(cal)
    results = run_simulation(cands, cbt._EXIT_RULE, caches, end)
    day_contrib = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_contrib[d] += r / n_slots
    return [day_contrib.get(d, 0.0) for d in cal]


def _block_boot(a, b, block=21, iters=5000, seed=0):
    """ΔSharpe (b−a) bootstrap CI over aligned daily series via block resampling."""
    rng = np.random.default_rng(seed)
    a = np.asarray(a); b = np.asarray(b)
    n = len(a)
    nb = math.ceil(n / block)
    deltas = []
    for _ in range(iters):
        starts = rng.integers(0, max(1, n - block), size=nb)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:n]
        deltas.append(_sharpe(b[idx]) - _sharpe(a[idx]))
    deltas = np.array(deltas)
    return float(np.percentile(deltas, 2.5)), float(np.percentile(deltas, 97.5)), \
        float((deltas <= 0).mean())


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

    arms = ["base4", "rs4", "base6"]
    stitched = {a: [] for a in arms}
    fy_sh = {a: {} for a in arms}

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
        rs_of = {}
        for c in cands:
            sdts, scmap = stock_dts.get(c.stock_code, ([], {}))
            rs_of[id(c)] = _rs(sdts, scmap, n_dts, n_cmap, c.entry_date)
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]

        base_order = sorted(cands, key=lambda c: c.entry_date)
        rs_order = sorted(cands, key=lambda c: (c.entry_date, -rs_of[id(c)]))

        exsim._MAX_LOW_CORR = 3
        r_b4 = _series(base_order, caches, stock_dts, n_dts, n_cmap, cal, 4, cfg.end)
        r_rs = _series(rs_order, caches, stock_dts, n_dts, n_cmap, cal, 4, cfg.end)
        exsim._MAX_LOW_CORR = 5
        r_b6 = _series(base_order, caches, stock_dts, n_dts, n_cmap, cal, 6, cfg.end)
        exsim._MAX_LOW_CORR = 3

        for a, r in (("base4", r_b4), ("rs4", r_rs), ("base6", r_b6)):
            fy_sh[a][cfg.label] = _sharpe(r)
            stitched[a] += r[1:]
        logger.info("  {} done", cfg.label)

    print("\n" + "=" * 72)
    print("CERTIFICATION — corrected wins vs production 4-slot book")
    print("=" * 72)
    base = np.asarray(stitched["base4"])
    for name, key in (("RS-high @4-slot", "rs4"), ("6-slot @baseline", "base6")):
        var = np.asarray(stitched[key])
        dsh = _sharpe(var) - _sharpe(base)
        lo, hi, p0 = _block_boot(base, var)
        wins = sum(1 for c in _FYS if c.label in fy_sh["base4"]
                   and fy_sh[key][c.label] > fy_sh["base4"][c.label])
        testable = sum(1 for c in _FYS if c.label in fy_sh["base4"])
        oos = fy_sh[key].get("FY2025", float("nan")) - fy_sh["base4"].get("FY2025", float("nan"))
        print(f"\n{name}:  point ΔSharpe {dsh:+.2f}")
        print(f"  G1 block-bootstrap 95% CI [{lo:+.2f}, {hi:+.2f}]  p(Δ≤0)={p0:.3f}  "
              f"-> {'PASS' if lo > 0 else 'FAIL'}")
        print(f"  G2 per-FY wins {wins}/{testable}  -> {'PASS' if wins >= 6 else 'FAIL'}")
        print(f"  G3 FY2025 OOS ΔSharpe {oos:+.2f}  -> {'PASS' if oos > 0 else 'FAIL'}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
