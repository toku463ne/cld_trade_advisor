"""Fill-order robustness: shuffle null vs corr-aware (least-correlated-first).

The 4-slot book SKIPS (doesn't queue) when full, so the realized trade set is
one path through the candidate pool that depends on the within-day fill order.
This:
  1. SHUFFLE NULL — K random within-day orders → distribution of stitched
     Sharpe/return/maxDD (the order-luck confidence band + permutation null).
  2. CORR-GREEDY — uses run_simulation's day_selector hook to fill each free
     slot with the candidate LEAST correlated (trailing-20 returns) to the
     CURRENTLY-held names. Tests the one selection principle with a mechanism
     (lower cross-corr → lower portfolio variance → higher Sharpe).
  3. Positions baseline (deterministic), corr-greedy, and RS-high against the
     shuffle null (percentile + one-sided permutation p-value).

OUTCOME (2026-05-23, 200 shuffles): the FILL ORDER dominates. Shuffle-null
stitched Sharpe mean +0.89 sd 0.19 (p5 +0.60, p95 +1.20); return p5-p95
+141%..+624%. That band is WIDER than every selection effect tested this month.
- baseline (deterministic, = shipped) Sharpe 0.84 → p44 (a slightly UNLUCKY
  draw; the shipped +0.84/+257% headline is below the median order).
- RS-high 0.93 → p59, perm p=0.41 — indistinguishable from random reorder
  (confirms the RS reject for the right reason).
- corr-greedy (least-corr-to-holdings, via the new run_simulation day_selector
  hook) 0.99 → p73, perm p=0.27 — best of the three, mechanism-consistent, but
  NOT significant; the ≤1-high-corr cap already captures most diversification.
- prefer_b0 (Sharpe 1.12, from confluence_bearish_select) ≈ p89 / perm p≈0.11 —
  strongest lead but still does not clear the fill-order null.
Verdict: at ~36 trades/yr no confluence SELECTION rule beats slot-contention
luck; capacity is structural (own null needed); universe expansion is the
unblocker. See memory project_confluence_fill_order_null.md.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_slot_order
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
_CORR_WIN = 20
_K = 200          # shuffle iterations
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


def _make_corr_selector(returns, didx, window=_CORR_WIN):
    def trailing(code, today):
        di = didx.get(code)
        if di is None:
            return None
        i = di.get(today)
        if i is None or i < window:
            return None
        return returns[code][i - window + 1:i + 1]

    def selector(today, cands, open_pos):
        held = list({p.candidate.stock_code for p in open_pos})
        held_r = [r for r in (trailing(h, today) for h in held) if r is not None]
        if not held_r:
            return cands

        def key(c):
            cr = trailing(c.stock_code, today)
            if cr is None:
                return 0.0
            best = 0.0
            for r in held_r:
                cc = np.corrcoef(cr, r)[0, 1]
                if not math.isnan(cc):
                    best = max(best, abs(cc))
            return best
        return sorted(cands, key=key)   # ascending → least-correlated first
    return selector


def _fy_returns(ordered_or_cands, exit_rule, caches, cfg, stock_dts, cal,
                day_selector=None):
    cal_set = set(cal)
    results = run_simulation(ordered_or_cands, exit_rule, caches, cfg.end,
                             day_selector=day_selector)
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

    st_base, st_corr, st_rs = [], [], []
    st_shuffle = [[] for _ in range(_K)]

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

        # returns map for the corr selector
        returns, didx = {}, {}
        for code, (dts, cmap) in stock_dts.items():
            cl = np.array([cmap[d] for d in dts])
            r = np.zeros_like(cl, dtype=float)
            if len(cl) > 1:
                r[1:] = cl[1:] / cl[:-1] - 1.0
            returns[code] = r
            didx[code] = {d: i for i, d in enumerate(dts)}

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
        sel = _make_corr_selector(returns, didx)

        base = sorted(cands, key=lambda c: c.entry_date)
        rs_order = sorted(cands, key=lambda c: (c.entry_date, -rs_of[id(c)]))

        st_base += _fy_returns(base, cbt._EXIT_RULE, caches, cfg, stock_dts, cal)[1:]
        st_corr += _fy_returns(base, cbt._EXIT_RULE, caches, cfg, stock_dts, cal, sel)[1:]
        st_rs   += _fy_returns(rs_order, cbt._EXIT_RULE, caches, cfg, stock_dts, cal)[1:]

        for k in range(_K):
            rng = random.Random(k)
            pool = cands[:]
            rng.shuffle(pool)
            st_shuffle[k] += _fy_returns(pool, cbt._EXIT_RULE, caches, cfg, stock_dts, cal)[1:]
        logger.info("  {} done ({} candidates, {} shuffles)", cfg.label, len(cands), _K)

    sh_dist = np.array([_sharpe(s) for s in st_shuffle])
    rt_dist = np.array([_metrics(s)[0] for s in st_shuffle])
    dd_dist = np.array([_metrics(s)[2] for s in st_shuffle])
    bm, cm, rm = _metrics(st_base), _metrics(st_corr), _metrics(st_rs)

    def _pct(dist, v):
        return 100.0 * (dist < v).mean()
    def _pval(dist, v):  # one-sided: P(shuffle >= v)
        return float((dist >= v).mean())

    print("\n" + "=" * 78)
    print(f"FILL-ORDER ROBUSTNESS — {_K} shuffles, 4-slot capital-aware book")
    print("=" * 78)
    print(f"\n[shuffle null] stitched Sharpe: mean {sh_dist.mean():+.2f} "
          f"sd {sh_dist.std():.2f} | p5 {np.percentile(sh_dist,5):+.2f} "
          f"p50 {np.percentile(sh_dist,50):+.2f} p95 {np.percentile(sh_dist,95):+.2f}")
    print(f"               stitched return: mean {rt_dist.mean()*100:+.0f}% "
          f"[p5 {np.percentile(rt_dist,5)*100:+.0f}%, p95 {np.percentile(rt_dist,95)*100:+.0f}%]")
    print(f"               max drawdown   : mean {dd_dist.mean()*100:.0f}% "
          f"[p5 {np.percentile(dd_dist,5)*100:.0f}%, p95 {np.percentile(dd_dist,95)*100:.0f}%]")

    print(f"\n{'arm':<14}{'Sharpe':>8}{'total%':>9}{'maxDD%':>8}{'pctile':>8}{'perm p':>9}")
    for name, m in (("baseline", bm), ("corr-greedy", cm), ("RS-high", rm)):
        print(f"{name:<14}{m[1]:>8.2f}{m[0]*100:>9.1f}{m[2]*100:>8.1f}"
              f"{_pct(sh_dist, m[1]):>7.0f}%{_pval(sh_dist, m[1]):>9.3f}")
    print("\n(pctile = where the arm's Sharpe sits in the shuffle null; "
          "perm p = P(random order ≥ arm).")
    print(" corr-greedy is real only if its perm p is small AND it beats the null p95.)")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
