"""Stage-1 pipeline SMOKE TEST — validate cluster→benchmark→candidate on a bridged slice.

Runs the EXACT production pipeline (cluster.py funcs + sign_benchmark.run_benchmark +
confluence candidate generation) on the ~300 bridged NEW codes for ONE FY, under a separate
`smoke2024` cluster-set label so it NEVER collides with the production `classifiedYYYY` sets.
Times the O(n²) correlation step so the full-2,785 rebuild cost can be extrapolated.

Prereq: `src.data.jq_ohlcv_bridge` already loaded the slice into ohlcv_1d.
Read/writes only: a `smoke2024` StockClusterRun/Member + SignBenchmarkRun/Event (a dedicated
label namespace — drop with one DELETE; does not touch the 225 classifiedYYYY data).

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.universe_expansion_smoke
"""
from __future__ import annotations

import datetime
import sys
import time

from loguru import logger
from sqlalchemy import func, select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.cluster import (_fiscal_label, build_distance_matrix, cluster_stocks,
                                   run_pair_corr, save_clusters)
from src.analysis.confluence_benchmark import _BULLISH, _N_GATE
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.analysis.regime_sign_backtest import _build_zs_map
from src.analysis.sign_benchmark import run_benchmark
from src.data.db import get_session
from src.data.jq_ohlcv_bridge import _read_tier
from src.data.jquants_collector import to_yf_code
from src.simulator.cache import DataCache

_LABEL = "smoke2024"
_STORED = _fiscal_label(_LABEL)        # save_clusters prepends "classified" → "classifiedsmoke2024"
_UTC = datetime.timezone.utc
_START = datetime.datetime(2024, 4, 1, tzinfo=_UTC)
_END = datetime.datetime(2025, 4, 1, tzinfo=_UTC)     # exclusive
_TIER = "docs/analysis/universe_expansion_tier.txt"


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    jq_codes = _read_tier(_TIER, 300, True)
    codes = [to_yf_code(c) for c in jq_codes]
    n = len(codes)
    logger.info("smoke slice: {} bridged NEW codes, FY2024 window {}..{}", n,
                _START.date(), _END.date())

    # ── Step 1: pair correlation (the O(n²) bottleneck) — TIMED ──────────────
    with get_session() as s:
        t0 = time.time()
        corr_run_id = run_pair_corr(s, codes, _START, _END)
        t_corr = time.time() - t0
        condensed, dist_codes = build_distance_matrix(s, corr_run_id)
        clusters = cluster_stocks(condensed, dist_codes)
        save_clusters(s, _LABEL, _START, _END, clusters, corr_run_id, 0.5, "1d", False)
    n_reps = len(clusters)
    logger.info("corr+cluster done in {:.1f}s: {} reps from {} codes (corr_run_id={})",
                t_corr, n_reps, len(dist_codes), corr_run_id)

    reps = cbt._stocks_for_fy(_STORED)
    logger.info("_stocks_for_fy('{}') → {} representatives", _STORED, len(reps))

    # ── Step 2: sign benchmark over reps (10 bullish signs) — TIMED ──────────
    t0 = time.time()
    run_ids = []
    for sign in _BULLISH:
        with get_session() as s:
            rid = run_benchmark(session=s, sign_type=sign, stock_codes=reps,
                                stock_set=_STORED, start=_START, end=_END, gran="1d",
                                window=20, valid_bars=5, trend_cap_days=30, zz_size=5,
                                zz_mid_size=2, proximity_pct=0.015, corr_mode="all")
            run_ids.append(rid)
    t_bench = time.time() - t0
    logger.info("benchmark done in {:.1f}s: {} runs for {} signs", t_bench, len(run_ids),
                len(_BULLISH))

    # ── Step 3: verify events for NEW codes + candidate generation ───────────
    with get_session() as s:
        ev = s.execute(
            select(SignBenchmarkEvent.stock_code, func.count())
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.stock_set == _STORED)
            .group_by(SignBenchmarkEvent.stock_code)).all()
        rows = s.execute(
            select(SignBenchmarkRun.sign_type, SignBenchmarkEvent.stock_code,
                   SignBenchmarkEvent.fired_at)
            .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
            .where(SignBenchmarkRun.stock_set == _STORED)).all()
    n_ev = sum(c for _, c in ev)
    logger.info("SignBenchmarkEvent: {} events across {} new codes", n_ev, len(ev))

    # confluence candidate generation on one representative (end-to-end check)
    from collections import defaultdict
    fires = defaultdict(list)
    for sg, st, fa in rows:
        fires[st].append((sg, fa.date() if hasattr(fa, "date") else fa))
    n_cand_total = 0
    sample = None
    with get_session() as s:
        n225 = DataCache("^N225", "1d")
        n225.load(s, _START - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE), _END)
        for code in reps:
            c = DataCache(code, "1d")
            c.load(s, _START - datetime.timedelta(days=cbt._LOOKBACK_DAYS_CACHE), _END)
            if not c.bars:
                continue
            cm = cbt._build_corr_map(c, n225)
            zm = _build_zs_map(c, n225)
            cands = cbt._candidates_for_stock(code, fires.get(code, []), c, cm, zm,
                                              _START.date(), _END.date(), _N_GATE)
            n_cand_total += len(cands)
            if cands and sample is None:
                sample = (code, len(cands), cands[0].entry_date)

    print("\n" + "=" * 88)
    print("STAGE-1 PIPELINE SMOKE TEST — 300 bridged NEW codes, FY2024, label 'smoke2024'")
    print("=" * 88)
    print(f"  bridge:     300 codes already in ohlcv_1d (validated separately)")
    print(f"  corr+clust: {t_corr:6.1f}s  → {n_reps} representatives from {len(dist_codes)} codes")
    print(f"  benchmark:  {t_bench:6.1f}s  → {len(run_ids)} runs, {n_ev} sign events on {len(ev)} codes")
    print(f"  candidates: {n_cand_total} confluence candidates (N≥{_N_GATE}) on the reps")
    if sample:
        print(f"              e.g. {sample[0]}: {sample[1]} candidates, first entry {sample[2]}")
    # extrapolate to the full tier
    full = 2785
    corr_scale = (full / n) ** 2          # O(n²) pairwise
    bench_scale = full / max(len(reps), 1)
    print(f"\n  EXTRAPOLATION to full tier (2,785 codes, 8 FYs):")
    print(f"    corr step  ~O(n²): {t_corr*corr_scale/60:.1f} min/FY × 8 ≈ "
          f"{t_corr*corr_scale*8/60:.0f} min  (the bottleneck)")
    print(f"    benchmark  ~O(n) : scales ~×{bench_scale:.0f} on reps → "
          f"{t_bench*bench_scale*8/60:.0f} min total (rough)")
    print(f"\n  cleanup: DELETE the 'smoke2024' StockClusterRun + SignBenchmarkRun(stock_set="
          f"'smoke2024') to remove this test; the 300 bridged ohlcv rows are harmless/reusable.")
    ok = n_reps > 0 and n_ev > 0 and n_cand_total > 0
    print(f"\n  VERDICT: {'PIPELINE OK end-to-end (cluster→benchmark→candidates all produced output)' if ok else 'BROKEN — see logs'}")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
