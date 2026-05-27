"""Stage-1 full rebuild — bridge the expanded tier + cluster + sign-benchmark (DB-mutating).

Executes the pre-registered pipeline (`docs/analysis/universe_expansion_stage1_preregistration.md`
§8) on the FULL frozen tier (2,785 codes) under an `expYYYY` cluster-set namespace
(`classifiedexpYYYY`) that coexists with the production 225 `classifiedYYYY` sets — so the
universe_expansion_null script can compare 225 vs expanded with NO disturbance to the 225 data.

Steps (all idempotent / resumable):
  1. bridge every NEW tier code's adjusted OHLCV into ohlcv_1d (the 209 already-225 codes keep
     their yfinance rows via on_conflict_do_nothing; the ~300 smoke codes are skipped likewise).
  2. BYPASS CLUSTERING (operator decision 2026-05-27): the pairwise-corr clustering OOM'd at
     2,785 codes (designed for 225) AND the smoke test showed it is ~identity here (298 reps from
     300 — it dedups almost nothing). So instead, the per-cluster-year universe = the tier codes
     with ≥150 bars in that FY-window, stored as a "universe-as-representatives" StockClusterRun
     (singleton clusters → every code is a representative) via save_clusters. No corr, no OOM.
     Diversification still enforced where it matters: the ≤1-high/≤5-low corr caps + corr-greedy
     fill at slot-selection. Most faithful to the pre-reg's "full tier, no cap".
  3. sign-benchmark the universe per FY2018–FY2025, BULLISH signs only (run_benchmark iterates
     per-code → memory-bounded), skipping runs that already exist.

Runtime ≈ 1 h (clustering bypassed → benchmark-dominated, ~6 min/FY × 8). Reversible: restore the
2026-05-27 market_data dump → 225 book; or DELETE the classifiedexp* cluster/benchmark runs.

Run: PYTHONPATH=. uv run --env-file devenv python -m src.analysis.universe_expansion_rebuild
"""
from __future__ import annotations

import datetime
import sys
import time

from loguru import logger
from sqlalchemy import func, select

import src.analysis.confluence_strategy_backtest as cbt
from src.analysis.cluster import _fiscal_dates, _fiscal_label, save_clusters
from src.analysis.confluence_benchmark import _BULLISH
from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun, StockClusterRun
from src.analysis.sign_benchmark import run_benchmark
from src.analysis.sign_benchmark_multiyear import (FY_CONFIG, _CLUSTER_YEARS_TO_BUILD,
                                                   _MIN_BARS, _count_bars, _dt,
                                                   _find_existing_run)
from src.data.db import get_session
from src.data.jq_ohlcv_bridge import _read_tier, bridge
from src.data.jquants_collector import to_yf_code

_TIER = "docs/analysis/universe_expansion_tier.txt"


def _token(year: str) -> str:
    return f"exp{year}"                       # save_clusters → classifiedexp{year}


def run() -> None:
    cbt._VALID_BARS = dict(_BULLISH)
    cbt._MULTIYEAR_MIN_RUN_ID = 0
    t_all = time.time()

    # ── Step 1: bridge every NEW tier code ───────────────────────────────────
    new_codes = _read_tier(_TIER, None, new_only=True)
    logger.info("STEP 1 — bridging {} NEW tier codes into ohlcv_1d …", len(new_codes))
    t0 = time.time()
    done, rows = bridge(new_codes)
    logger.info("STEP 1 done in {:.0f}s: {} codes, {} rows inserted", time.time() - t0, done, rows)

    tier_codes = [to_yf_code(c) for c in _read_tier(_TIER, None, new_only=False)]
    logger.info("tier universe for clustering: {} yf codes", len(tier_codes))

    # ── Step 2: universe-as-representatives per cluster-year (NO clustering) ──
    for Y in _CLUSTER_YEARS_TO_BUILD:
        label = _fiscal_label(_token(Y))     # classifiedexpY
        with get_session() as s:
            exists = s.execute(select(StockClusterRun.id)
                               .where(StockClusterRun.fiscal_year == label)).scalar_one_or_none()
        if exists:
            logger.info("STEP 2 — {} already built (run {}) — skip", label, exists)
            continue
        start, end = _fiscal_dates(Y)
        with get_session() as s:
            bar_cnts = _count_bars(s, tier_codes, start, end)
        codes_Y = [c for c in tier_codes if bar_cnts.get(c, 0) >= _MIN_BARS]
        # singleton clusters → save_clusters marks every code its own representative (no corr)
        clusters = {i: [c] for i, c in enumerate(codes_Y)}
        t0 = time.time()
        with get_session() as s:
            save_clusters(s, _token(Y), start, end, clusters, None, 0.0, "1d", False)
        logger.info("STEP 2 — {} built in {:.0f}s: {} tier codes (≥{} bars in FY-{})",
                    label, time.time() - t0, len(codes_Y), _MIN_BARS, Y)

    # ── Step 3: sign-benchmark per FY (bullish signs, resumable) ─────────────
    for fy_label, start_str, end_str, cluster_year in FY_CONFIG:
        cluster_set = _fiscal_label(_token(cluster_year))
        bench_start, bench_end = _dt(start_str), _dt(end_str)
        reps = cbt._stocks_for_fy(cluster_set)
        if not reps:
            logger.warning("STEP 3 — no reps for {} ({}) — skip", fy_label, cluster_set)
            continue
        with get_session() as s:
            bar_cnts = _count_bars(s, reps, bench_start, bench_end + datetime.timedelta(days=1))
        codes = [c for c in reps if bar_cnts.get(c, 0) >= _MIN_BARS]
        logger.info("STEP 3 — {} ({}): {}/{} reps have ≥{} bars; benchmarking {} bullish signs",
                    fy_label, cluster_set, len(codes), len(reps), _MIN_BARS, len(_BULLISH))
        t0 = time.time()
        for sign in _BULLISH:
            with get_session() as s:
                if _find_existing_run(s, sign, cluster_set, bench_start, bench_end):
                    continue
            with get_session() as s:
                run_benchmark(session=s, sign_type=sign, stock_codes=codes,
                              stock_set=cluster_set, start=bench_start, end=bench_end,
                              gran="1d", window=20, valid_bars=5, trend_cap_days=30,
                              zz_size=5, zz_mid_size=2, proximity_pct=0.015, corr_mode="all")
        logger.info("STEP 3 — {} done in {:.0f}s", fy_label, time.time() - t0)

    # ── summary ──────────────────────────────────────────────────────────────
    with get_session() as s:
        ncl = s.execute(select(func.count()).select_from(StockClusterRun)
                        .where(StockClusterRun.fiscal_year.like("classifiedexp%"))).scalar()
        nbm = s.execute(select(func.count()).select_from(SignBenchmarkRun)
                        .where(SignBenchmarkRun.stock_set.like("classifiedexp%"))).scalar()
        nev = s.execute(select(func.count()).select_from(SignBenchmarkEvent)
                        .join(SignBenchmarkRun, SignBenchmarkRun.id == SignBenchmarkEvent.run_id)
                        .where(SignBenchmarkRun.stock_set.like("classifiedexp%"))).scalar()
    print("\n" + "=" * 80)
    print("STAGE-1 REBUILD COMPLETE")
    print("=" * 80)
    print(f"  expanded cluster sets : {ncl}  (classifiedexp2017..classifiedexp2024)")
    print(f"  expanded benchmark runs: {nbm}  (bullish signs × FY)")
    print(f"  expanded sign events   : {nev}")
    print(f"  total wall time        : {(time.time() - t_all)/60:.0f} min")
    print(f"\n  NEXT: write + run src/analysis/universe_expansion_null.py (gates A + B), using the")
    print(f"        classifiedexpYYYY sets vs the 225 classifiedYYYY sets.")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    run()
