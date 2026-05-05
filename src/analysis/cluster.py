"""Nikkei 225 stock clustering by return correlation.

Pipeline
--------
1. Collect OHLCV for the fiscal year (optional, --collect).
2. Run sliding-window pair correlation and store in DB (optional, --run-corr).
3. Build symmetric distance matrix from ``stock_corr_pairs``:
       d(a, b) = 1 - max(0, |mean_corr| - std_corr)
   Lower distance = higher-confidence correlation.
4. Agglomerative clustering (average linkage, precomputed distance).
5. Pick the stock with the highest total period volume as cluster representative.
6. Persist ``StockClusterRun`` + ``StockClusterMember`` rows.

CLI
---
    # Full pipeline for 2023年度:
    uv run --env-file devenv python -m src.analysis.cluster \\
        --fiscal-year 2023 --collect --run-corr

    # Cluster only (corr data already in DB as corr_run_id 7):
    uv run --env-file devenv python -m src.analysis.cluster \\
        --fiscal-year 2023 --corr-run-id 7

    # Custom threshold and print dendrogram stats without saving:
    uv run --env-file devenv python -m src.analysis.cluster \\
        --fiscal-year 2023 --corr-run-id 7 --threshold 0.3 --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger
from scipy.cluster.hierarchy import dendrogram, fcluster, linkage
from scipy.spatial.distance import squareform
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.analysis.models import CorrRun, StockClusterMember, StockClusterRun, StockCorrPair
from src.config import load_stock_codes
from src.data.collect import OHLCVCollector
from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP
from src.data.nikkei225 import load_or_fetch
from src.analysis.stock_corrs import run_and_save as run_corr_and_save

_DEFAULT_INI   = Path("configs/nikkei225.ini")
_DEFAULT_GRAN  = "1d"
_DEFAULT_WINDOW = 20
_DEFAULT_STEP   = 10
_DEFAULT_THRESH = 0.3

FISCAL_YEARS: dict[str, tuple[str, str]] = {
    "2023": ("2023-04-01", "2024-03-31"),
    "2024": ("2024-04-01", "2025-03-31"),
    "2025": ("2025-04-01", "2026-03-31"),
}


# ---------------------------------------------------------------------------
# Fiscal year helpers
# ---------------------------------------------------------------------------

def _fiscal_dates(fiscal_year: str) -> tuple[datetime.datetime, datetime.datetime]:
    if fiscal_year not in FISCAL_YEARS:
        raise ValueError(f"Unknown fiscal_year {fiscal_year!r}. Choose from {list(FISCAL_YEARS)}")
    s, e = FISCAL_YEARS[fiscal_year]
    tz = datetime.timezone.utc
    return (
        datetime.datetime.fromisoformat(s).replace(tzinfo=tz),
        datetime.datetime.fromisoformat(e).replace(tzinfo=tz) + datetime.timedelta(days=1),
    )


def _fiscal_label(fiscal_year: str) -> str:
    return f"classified{fiscal_year}"


# ---------------------------------------------------------------------------
# Step 1: collect OHLCV
# ---------------------------------------------------------------------------

def collect_ohlcv(
    session: Session,
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str = _DEFAULT_GRAN,
) -> None:
    """Download and store OHLCV for all codes over [start, end)."""
    collector = OHLCVCollector(session)
    n_ok, n_skip = 0, 0
    for i, code in enumerate(codes, 1):
        try:
            inserted = collector.collect(code, gran, start, end)
            if inserted:
                n_ok += 1
            else:
                n_skip += 1
        except Exception as exc:
            logger.warning("Failed to collect {}: {}", code, exc)
        if i % 20 == 0:
            logger.info("Collected {}/{} stocks …", i, len(codes))
    logger.info("Collection done: {} inserted, {} skipped/cached", n_ok, n_skip)


# ---------------------------------------------------------------------------
# Step 2: run pair correlation (delegates to stock_corrs)
# ---------------------------------------------------------------------------

def run_pair_corr(
    session: Session,
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    window_days: int = _DEFAULT_WINDOW,
    step_days: int   = _DEFAULT_STEP,
    gran: str        = _DEFAULT_GRAN,
) -> int:
    """Run sliding-window correlation and return the new corr_run.id."""
    run_id = run_corr_and_save(session, codes, start, end, window_days, step_days, gran)
    logger.info("Saved correlation run id={}", run_id)
    return run_id


# ---------------------------------------------------------------------------
# Step 3: build distance matrix
# ---------------------------------------------------------------------------

def build_distance_matrix(
    session: Session,
    corr_run_id: int,
) -> tuple[np.ndarray, list[str]]:
    """Return (condensed_dist, codes) from the given CorrRun.

    d(a, b) = 1 - max(0, |mean_corr| - std_corr)  ∈ [0, 1]
    Missing pairs (no valid windows) get distance 1.0.
    """
    pairs = session.execute(
        select(StockCorrPair).where(StockCorrPair.corr_run_id == corr_run_id)
    ).scalars().all()

    if not pairs:
        raise RuntimeError(f"No StockCorrPair rows for corr_run_id={corr_run_id}")

    codes_set: set[str] = set()
    for p in pairs:
        codes_set.add(p.stock_a)
        codes_set.add(p.stock_b)
    codes = sorted(codes_set)
    n     = len(codes)
    idx   = {c: i for i, c in enumerate(codes)}

    dist  = np.ones((n, n), dtype=np.float64)
    np.fill_diagonal(dist, 0.0)

    for p in pairs:
        confidence = max(0.0, abs(p.mean_corr) - p.std_corr)
        d = 1.0 - confidence
        i, j = idx[p.stock_a], idx[p.stock_b]
        dist[i, j] = d
        dist[j, i] = d

    condensed = squareform(dist, checks=False)
    return condensed, codes


# ---------------------------------------------------------------------------
# Step 4: agglomerative clustering
# ---------------------------------------------------------------------------

def cluster_stocks(
    condensed_dist: np.ndarray,
    codes: list[str],
    threshold: float = _DEFAULT_THRESH,
) -> dict[int, list[str]]:
    """Run agglomerative clustering and return cluster_id → [stock_codes]."""
    Z = linkage(condensed_dist, method="average")
    labels = fcluster(Z, t=threshold, criterion="distance")

    clusters: dict[int, list[str]] = {}
    for code, lbl in zip(codes, labels):
        clusters.setdefault(int(lbl), []).append(code)
    return clusters


def print_dendrogram_summary(
    condensed_dist: np.ndarray,
    threshold: float = _DEFAULT_THRESH,
) -> None:
    """Print cluster-count at several thresholds to help choose one."""
    Z = linkage(condensed_dist, method="average")
    for t in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]:
        labels   = fcluster(Z, t=t, criterion="distance")
        n_clust  = len(set(labels))
        singletons = sum(1 for c in np.unique(labels) if np.sum(labels == c) == 1)
        logger.info(
            "threshold={:.1f}  clusters={}  singletons={}",
            t, n_clust, singletons,
        )


# ---------------------------------------------------------------------------
# Step 5: select representative by volume
# ---------------------------------------------------------------------------

def _total_volumes(
    session: Session,
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str = _DEFAULT_GRAN,
) -> dict[str, float]:
    """Return total volume for each code over the period."""
    model = OHLCV_MODEL_MAP[gran]
    rows  = session.execute(
        select(model.stock_code, func.sum(model.volume).label("total_vol"))
        .where(model.stock_code.in_(codes), model.ts >= start, model.ts < end)
        .group_by(model.stock_code)
    ).all()
    return {r.stock_code: float(r.total_vol) for r in rows}


def _bar_counts(
    session: Session,
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str = _DEFAULT_GRAN,
) -> dict[str, int]:
    model = OHLCV_MODEL_MAP[gran]
    rows  = session.execute(
        select(model.stock_code, func.count().label("n"))
        .where(model.stock_code.in_(codes), model.ts >= start, model.ts < end)
        .group_by(model.stock_code)
    ).all()
    return {r.stock_code: int(r.n) for r in rows}


# ---------------------------------------------------------------------------
# Step 6: persist to DB
# ---------------------------------------------------------------------------

def save_clusters(
    session: Session,
    fiscal_year: str,
    start: datetime.datetime,
    end: datetime.datetime,
    clusters: dict[int, list[str]],
    corr_run_id: int | None,
    threshold: float,
    gran: str = _DEFAULT_GRAN,
    dry_run: bool = False,
) -> None:
    """Persist StockClusterRun + StockClusterMember, replacing any prior run for this year."""
    all_codes = [c for members in clusters.values() for c in members]
    volumes   = _total_volumes(session, all_codes, start, end, gran)
    bar_cnts  = _bar_counts(session, all_codes, start, end, gran)
    label     = _fiscal_label(fiscal_year)
    now       = datetime.datetime.now(datetime.timezone.utc)

    n_stocks   = len(all_codes)
    n_clusters = len(clusters)

    logger.info(
        "Clustering: {} stocks → {} clusters  (threshold={})",
        n_stocks, n_clusters, threshold,
    )
    for cid in sorted(clusters)[:10]:
        members = clusters[cid]
        rep = max(members, key=lambda c: volumes.get(c, 0.0))
        logger.info("  cluster {:>4d}: {} stocks, representative={}", cid, len(members), rep)
    if n_clusters > 10:
        logger.info("  … {} more clusters", n_clusters - 10)

    if dry_run:
        logger.info("Dry run — not saving to DB.")
        return

    # Remove prior run for this fiscal year if it exists
    prior = session.execute(
        select(StockClusterRun).where(StockClusterRun.fiscal_year == label)
    ).scalar_one_or_none()
    if prior:
        session.delete(prior)
        session.flush()

    run = StockClusterRun(
        fiscal_year = label,
        start_dt    = start,
        end_dt      = end,
        corr_run_id = corr_run_id,
        threshold   = threshold,
        n_stocks    = n_stocks,
        n_clusters  = n_clusters,
        created_at  = now,
    )
    session.add(run)
    session.flush()

    member_rows: list[dict[str, Any]] = []
    for cid, members in clusters.items():
        rep = max(members, key=lambda c: volumes.get(c, 0.0))
        for code in members:
            member_rows.append({
                "run_id":           run.id,
                "fiscal_year":      label,
                "stock_code":       code,
                "cluster_id":       cid,
                "is_representative": code == rep,
                "total_volume":     volumes.get(code),
                "n_bars":           bar_cnts.get(code),
            })

    session.bulk_insert_mappings(StockClusterMember, member_rows)  # type: ignore[arg-type]
    session.commit()
    logger.info("Saved cluster run id={} for {}", run.id, label)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_pipeline(
    fiscal_year: str,
    threshold: float       = _DEFAULT_THRESH,
    corr_run_id: int | None = None,
    collect: bool          = False,
    run_corr: bool         = False,
    window_days: int       = _DEFAULT_WINDOW,
    step_days: int         = _DEFAULT_STEP,
    gran: str              = _DEFAULT_GRAN,
    dry_run: bool          = False,
) -> None:
    start, end = _fiscal_dates(fiscal_year)
    codes = load_or_fetch()
    logger.info("Fiscal year {}年度: {} stocks, {} → {}", fiscal_year, len(codes), start.date(), end.date())

    with get_session() as session:
        if collect:
            logger.info("Step 1: collecting OHLCV …")
            collect_ohlcv(session, codes, start, end, gran)

        if run_corr:
            logger.info("Step 2: running pair correlation …")
            corr_run_id = run_pair_corr(session, codes, start, end, window_days, step_days, gran)

        if corr_run_id is None:
            # Try to find a matching corr run automatically
            run = session.execute(
                select(CorrRun)
                .where(CorrRun.start_dt <= start, CorrRun.end_dt >= end)
                .order_by(CorrRun.created_at.desc())
            ).scalar_one_or_none()
            if run is None:
                raise RuntimeError(
                    "No corr_run_id given and no matching CorrRun found. "
                    "Run with --run-corr first or pass --corr-run-id."
                )
            corr_run_id = run.id
            logger.info("Using corr_run_id={} (auto-detected)", corr_run_id)

        logger.info("Step 3: building distance matrix for corr_run_id={} …", corr_run_id)
        condensed, dist_codes = build_distance_matrix(session, corr_run_id)
        logger.info("Distance matrix: {} × {}", len(dist_codes), len(dist_codes))

        logger.info("Dendrogram summary:")
        print_dendrogram_summary(condensed, threshold)

        logger.info("Step 4: clustering (threshold={}) …", threshold)
        clusters = cluster_stocks(condensed, dist_codes, threshold)

        logger.info("Step 5: saving clusters …")
        save_clusters(session, fiscal_year, start, end, clusters, corr_run_id, threshold, gran, dry_run)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(
        prog="python -m src.analysis.cluster",
        description="Cluster Nikkei 225 stocks by return correlation for a fiscal year.",
    )
    p.add_argument("--fiscal-year", required=True, choices=list(FISCAL_YEARS),
                   help="Japanese fiscal year (e.g. 2023 = 2023/4/1–2024/3/31)")
    p.add_argument("--threshold", type=float, default=_DEFAULT_THRESH,
                   help="Agglomerative distance threshold (default: 0.3)")
    p.add_argument("--corr-run-id", type=int, default=None,
                   help="Use an existing CorrRun from DB (skip --run-corr)")
    p.add_argument("--collect",  action="store_true", help="Download OHLCV first")
    p.add_argument("--run-corr", action="store_true", help="Compute pair correlations first")
    p.add_argument("--window",   type=int, default=_DEFAULT_WINDOW, help="Correlation window (bars)")
    p.add_argument("--step",     type=int, default=_DEFAULT_STEP,   help="Correlation step (bars)")
    p.add_argument("--gran",     default=_DEFAULT_GRAN, help="Granularity (default: 1d)")
    p.add_argument("--dry-run",  action="store_true", help="Compute but do not write to DB")

    args = p.parse_args(argv)
    run_pipeline(
        fiscal_year  = args.fiscal_year,
        threshold    = args.threshold,
        corr_run_id  = args.corr_run_id,
        collect      = args.collect,
        run_corr     = args.run_corr,
        window_days  = args.window,
        step_days    = args.step,
        gran         = args.gran,
        dry_run      = args.dry_run,
    )


if __name__ == "__main__":
    main()
