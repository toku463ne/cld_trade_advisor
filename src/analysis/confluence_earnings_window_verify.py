"""Verification pass for confluence_earnings_window_stage0 (read-only).

The Stage-0 pooled stats counted each position once PER FILL ORDER (K=20) — ~20x
duplication inflates n and could mask year- or outlier-concentration. This pass:

  1. Dedupes to UNIQUE positions (stock, entry, exit) across the 20 orders before
     recomputing the bucket table, held-through cohort, and PnL decomposition.
  2. Per-FY W0 (reaction-bar) decomposition — is the +16.9% PnL share one year?
  3. Significance with honest clustering: per-position announcement-window PnL,
     t-test across unique positions (through − not), and a sign-flip count of the
     W0 per-bar mean across FYs.
  4. Spot checks: the 5 largest |reaction-bar| daily returns held by the book,
     printed with the matching jq_statements disclosure row (date/time/doc) so they
     can be eyeballed against a chart.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.confluence_earnings_window_verify
"""
from __future__ import annotations

import datetime
import random
from collections import defaultdict

import numpy as np
from loguru import logger
from sqlalchemy import select

import src.analysis.confluence_strategy_backtest as cbt
import src.exit.exit_simulator as exsim
from src.analysis.confluence_benchmark import _FYS
from src.analysis.confluence_evtilt_null import _closes, _pos_daily
from src.analysis.confluence_pead_inclusion_ab import _load_pead_statements
from src.analysis.confluence_earnings_window_stage0 import (
    _bucket, _coh_line, _dist_to_next, _impact_index, _BUCKETS, _NEAR, _FAR_MIN,
)
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.data.db import get_session
from src.exit.exit_simulator import run_simulation
from src.simulator.cache import DataCache
from src.strategy.confluence_sign import _BULLISH_SIGNS

_K_ORDERS = 20
_N_GATE = 3


def run() -> None:
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    exsim._MAX_LOW_CORR = 5
    with get_session() as s:
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.sign_type.in_(list(_BULLISH_SIGNS)))).all()
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
    stmts = _load_pead_statements()

    buckets: defaultdict[str, list[float]] = defaultdict(list)
    coh_through: dict[bool, list[float]] = {True: [], False: []}
    pos_ann_pnl: list[tuple[float, float]] = []   # (ann_window_pnl, other_pnl) per unique pos
    decomp_fy: dict[str, dict[str, list[float]]] = {}
    spot: list[tuple] = []   # (abs_r, r, code, date, fy)

    for cfg in _FYS:
        codes = cbt._stocks_for_fy(cfg.stock_set)
        if not codes:
            continue
        ss = cfg.start - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE + 90)
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
        stock_dts = {code: _closes(c) for code, c in caches.items()}
        impact_ix = _impact_index(stmts, caches)
        cal_of = {code: sorted({b.dt.date() for b in c.bars}) for code, c in caches.items()}

        cands = []
        for code in caches:
            cands.extend(cbt._candidates_for_stock(
                code, fires.get(code, []), caches[code],
                corr_maps.get(code, {}), zs_maps.get(code, {}), cfg.start, cfg.end, _N_GATE))

        seen: set[tuple] = set()
        fy_dec = {"W0": [], "W1": [], "other": []}
        for seed in range(_K_ORDERS):
            pool = cands[:]
            random.Random(seed).shuffle(pool)
            for p in run_simulation(pool, cbt._EXIT_RULE, caches, cfg.end):
                key = (p.stock_code, p.entry_date, p.exit_date)
                if key in seen or not p.entry_price:
                    continue
                seen.add(key)
                ret = p.exit_price / p.entry_price - 1.0
                dist = _dist_to_next(p.stock_code, p.entry_date, impact_ix, cal_of)
                if dist is not None:
                    buckets[_bucket(dist)].append(ret)
                impacts, idx = impact_ix.get(p.stock_code, ([], {}))
                imp_set = set(impacts)
                cal = cal_of.get(p.stock_code, [])
                imp_next = {cal[idx[d] + 1] for d in impacts if idx[d] + 1 < len(cal)}
                through, ann_pnl, oth_pnl = False, 0.0, 0.0
                for d, r in _pos_daily(p, *stock_dts.get(p.stock_code, ([], {}))).items():
                    if d in imp_set:
                        fy_dec["W0"].append(r); through = True; ann_pnl += r
                        spot.append((abs(r), r, p.stock_code, d, cfg.label))
                    elif d in imp_next:
                        fy_dec["W1"].append(r); ann_pnl += r
                    else:
                        fy_dec["other"].append(r); oth_pnl += r
                coh_through[through].append(ret)
                pos_ann_pnl.append((ann_pnl, oth_pnl))
        decomp_fy[cfg.label] = fy_dec
        logger.info("  {} done ({} unique positions)", cfg.label, len(seen))

    # ---- report -------------------------------------------------------------
    print("\n=== VERIFY: unique-position recomputation ===")
    print("(A) bucket table (UNIQUE positions, union of 20 orders):")
    for lab in [b[2] for b in _BUCKETS] + [">40/none"]:
        print(_coh_line(lab, buckets.get(lab, [])))
    near = buckets.get("1-5", []) + buckets.get("6-10", [])
    far = buckets.get("21-40", []) + buckets.get(">40/none", [])
    if near and far:
        print(f"   NEAR−FAR (unique): {(np.mean(near)-np.mean(far))*100:+.2f}pp "
              f"(n={len(near)} vs {len(far)})")

    print("\n(B) decomposition per FY (unique positions): W0 = reaction bar")
    w0_means = []
    for fy, dec in decomp_fy.items():
        w0, w1, oth = (np.asarray(dec[k]) for k in ("W0", "W1", "other"))
        tot = w0.sum() + w1.sum() + oth.sum()
        share = w0.sum() / tot * 100 if tot != 0 else float("nan")
        w0_means.append(w0.mean() if len(w0) else float("nan"))
        print(f"  {fy:<8} W0 bars {len(w0):>4} ({len(w0)/(len(w0)+len(w1)+len(oth))*100:4.1f}%)  "
              f"W0 mean {w0.mean()*100:+.3f}%  other mean {oth.mean()*100:+.3f}%  "
              f"W0 PnL share {share:+6.1f}%  (book PnL sum {tot*100:+.0f}%)")
    pos_fy = sum(1 for m in w0_means if m > 0)
    print(f"  W0 per-bar mean positive in {pos_fy}/{len(w0_means)} FYs")

    w0_all = np.asarray([r for d in decomp_fy.values() for r in d["W0"]])
    oth_all = np.asarray([r for d in decomp_fy.values() for r in d["other"]])
    se = np.sqrt(w0_all.var() / len(w0_all) + oth_all.var() / len(oth_all))
    print(f"\n  pooled unique W0 mean {w0_all.mean()*100:+.3f}% (n={len(w0_all)}) vs "
          f"other {oth_all.mean()*100:+.3f}% (n={len(oth_all)}); diff t≈{(w0_all.mean()-oth_all.mean())/se:.2f}")
    # outlier sensitivity: drop top/bottom 1% of W0 bars
    if len(w0_all) > 100:
        lo, hi = np.percentile(w0_all, [1, 99])
        trimmed = w0_all[(w0_all >= lo) & (w0_all <= hi)]
        print(f"  W0 mean after trimming 1% tails: {trimmed.mean()*100:+.3f}% (n={len(trimmed)})")

    print("\n(C) held-through cohort (unique):")
    print(_coh_line("through", coh_through[True]))
    print(_coh_line("not-through", coh_through[False]))
    a, b = np.asarray(coh_through[True]), np.asarray(coh_through[False])
    se = np.sqrt(a.var() / len(a) + b.var() / len(b))
    print(f"   diff {(a.mean()-b.mean())*100:+.2f}pp  t≈{(a.mean()-b.mean())/se:.2f}")
    ann = np.asarray([x for x, _ in pos_ann_pnl])
    print(f"   per-position announcement-window PnL: mean {ann.mean()*100:+.3f}%  "
          f"t≈{ann.mean()/ (ann.std()/np.sqrt(len(ann))):.2f}  (n={len(ann)} unique positions)")

    print("\n(D) spot checks — 5 largest |reaction-bar| returns held by the book:")
    spot.sort(reverse=True)
    with get_session() as s:
        from src.data.jquants_models import JqStatement
        from src.data.jquants_collector import to_yf_code
        rows = s.execute(select(JqStatement.local_code, JqStatement.disclosed_date,
                                JqStatement.disclosed_time, JqStatement.type_of_document)).all()
    by_code: defaultdict[str, list] = defaultdict(list)
    for lc, dd, dt, tod in rows:
        by_code[to_yf_code(lc)].append((dd, dt, tod))
    for _, r, code, d, fy in spot[:5]:
        match = [(dd, dt, tod) for dd, dt, tod in by_code.get(code, [])
                 if datetime.timedelta(days=0) <= d - dd <= datetime.timedelta(days=3)]
        print(f"  {fy} {code} {d}  r={r*100:+.1f}%  disclosures≤3d-before: "
              + ("; ".join(f"{dd} {dt} {str(tod)[:28]}" for dd, dt, tod in match[:2]) or "NONE ← BUG?"))
    print()


if __name__ == "__main__":
    run()
