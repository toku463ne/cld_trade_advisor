"""Fill-order null for the RegimeSign strategy (capital-aware 6-slot book).

Why
---
`regime_sign_backtest.md` reports PER-TRADE Sharpe (aggregate +3.10), which
CLAUDE.md flags as NOT the portfolio metric — it routinely overstates because
it ignores slot contention and capital weighting.  This script computes the
capital-aware, FILL-ORDER-robust portfolio Sharpe so RegimeSign can be compared
apples-to-apples against the Confluence strategy's published fill-order band
(`confluence_slot_order.py`: shuffle Sharpe mean +0.89, sd 0.19).

What it does
------------
The 6-slot book (≤1 high-corr + ≤5 low/mid, per exit_simulator) SKIPS — not
queues — when full, so the realized trade set is one path through the candidate
pool that depends on within-day fill order.

  1. SHUFFLE NULL — K=200 random within-day orders → distribution of stitched
     capital-aware portfolio Sharpe / total return / maxDD (the order-luck band).
  2. BASELINE — the deterministic shipped order (candidates sorted by entry_date)
     placed inside that null → percentile + one-sided perm p-value.

Candidate pool is the EXACT shipped RegimeSign set
(`regime_sign_backtest.build_fy_candidates`), so the null and the backtest
share an identical pool.  Read-only — writes nothing, prints a table.

Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.regime_sign_fill_order_null
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

from src.analysis.regime_sign_backtest import (
    EXIT_RULE,
    RS_FY_CONFIGS,
    build_fy_candidates,
)
from src.exit.exit_simulator import _MAX_HIGH_CORR, _MAX_LOW_CORR, run_simulation
from src.simulator.cache import DataCache

_K = 200                                  # shuffle iterations
_SLOTS = _MAX_HIGH_CORR + _MAX_LOW_CORR   # 6-slot equal-weight book


def _closes(cache: DataCache):
    dts, cmap, seen = [], {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d); dts.append(d); cmap[d] = b.close
    dts.sort()
    return dts, cmap


def _pos_daily(p, dts, cmap):
    """Per-day return contribution of one closed position over its holding span."""
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


def _fy_returns(cands, caches, cfg, stock_dts, cal):
    """Stitched per-calendar-day portfolio return for one ordered candidate list."""
    cal_set = set(cal)
    results = run_simulation(cands, EXIT_RULE, caches, cfg.end)
    day_contrib = defaultdict(float)
    for p in results:
        sdts, scmap = stock_dts.get(p.stock_code, ([], {}))
        for d, r in _pos_daily(p, sdts, scmap).items():
            if d in cal_set:
                day_contrib[d] += r / _SLOTS
    return [day_contrib.get(d, 0.0) for d in cal]


def run() -> None:
    st_base: list[float] = []
    st_shuffle: list[list[float]] = [[] for _ in range(_K)]

    for cfg in RS_FY_CONFIGS:
        cs = build_fy_candidates(cfg)
        if not cs.candidates or cs.n225_cache is None:
            logger.info("  {} skipped (no candidates)", cfg.label)
            continue

        caches = cs.stock_caches
        n_dts, _ = _closes(cs.n225_cache)
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        cal = [d for d in n_dts if cfg.start <= d <= cfg.end]
        if len(cal) < 2:
            continue

        base = sorted(cs.candidates, key=lambda c: c.entry_date)
        # drop day-1 (no prior bar) to match confluence_slot_order stitching
        st_base += _fy_returns(base, caches, cfg, stock_dts, cal)[1:]

        for k in range(_K):
            rng = random.Random(k)
            pool = cs.candidates[:]
            rng.shuffle(pool)
            st_shuffle[k] += _fy_returns(pool, caches, cfg, stock_dts, cal)[1:]
        logger.info("  {} done ({} candidates, {} shuffles)",
                    cfg.label, len(cs.candidates), _K)

    sh_dist = np.array([_sharpe(s) for s in st_shuffle])
    rt_dist = np.array([_metrics(s)[0] for s in st_shuffle])
    dd_dist = np.array([_metrics(s)[2] for s in st_shuffle])
    bm = _metrics(st_base)

    def _pct(dist, v):
        return 100.0 * (dist < v).mean()

    def _pval(dist, v):   # one-sided: P(shuffle >= v)
        return float((dist >= v).mean())

    print("\n" + "=" * 78)
    print(f"REGIME_SIGN FILL-ORDER NULL — {_K} shuffles, {_SLOTS}-slot capital-aware book")
    print(f"(FY{RS_FY_CONFIGS[0].label[2:]}–FY{RS_FY_CONFIGS[-1].label[2:]}, "
          f"stitched daily portfolio returns)")
    print("=" * 78)
    print(f"\n[shuffle null] stitched Sharpe: mean {sh_dist.mean():+.2f} "
          f"sd {sh_dist.std():.2f} | p5 {np.percentile(sh_dist,5):+.2f} "
          f"p50 {np.percentile(sh_dist,50):+.2f} p95 {np.percentile(sh_dist,95):+.2f}")
    print(f"               stitched return: mean {rt_dist.mean()*100:+.0f}% "
          f"[p5 {np.percentile(rt_dist,5)*100:+.0f}%, p95 {np.percentile(rt_dist,95)*100:+.0f}%]")
    print(f"               max drawdown   : mean {dd_dist.mean()*100:.0f}% "
          f"[p5 {np.percentile(dd_dist,5)*100:.0f}%, p95 {np.percentile(dd_dist,95)*100:.0f}%]")

    print(f"\n{'arm':<14}{'Sharpe':>8}{'total%':>9}{'maxDD%':>8}{'pctile':>8}{'perm p':>9}")
    print(f"{'baseline':<14}{bm[1]:>8.2f}{bm[0]*100:>9.1f}{bm[2]*100:>8.1f}"
          f"{_pct(sh_dist, bm[1]):>7.0f}%{_pval(sh_dist, bm[1]):>9.3f}")
    print("\n(baseline = deterministic shipped order, entry_date-sorted. pctile = where it "
          "sits in the shuffle null.)")
    print(" Compare the shuffle-null mean Sharpe to Confluence's published +0.89 band.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
