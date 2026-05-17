"""sign_benchmark_multiyear — Cross-fiscal-year benchmark for all sign detectors.

FY mapping (fiscal year → cluster set → benchmark period):
  FY2018: classified2017 · 2018-04-01 – 2019-03-31
  FY2019: classified2018 · 2019-04-01 – 2020-03-31
  FY2020: classified2019 · 2020-04-01 – 2021-03-31
  FY2021: classified2020 · 2021-04-01 – 2022-03-31
  FY2022: classified2021 · 2022-04-01 – 2023-03-31
  FY2023: classified2022 · 2023-04-01 – 2024-03-31
  FY2024: classified2023 · 2024-04-01 – 2025-03-31
  FY2025: classified2024 · 2025-04-01 – 2026-03-31  (out-of-sample backtest year)

Phases (all by default; use --phase to run a subset):
  download  — collect 1d OHLCV 2017-04-01 → 2026-03-31
  cluster   — build classified2017 … classified2022
  benchmark — 13 signs × FYs (skips existing runs)
  validate  — permutation test + regime split per run
  report    — append per-FY + aggregate tables to src/analysis/benchmark.md
  backtest  — evaluate FY2025 events through FY2018-FY2024 regime ranking

Usage:
    uv run --env-file devenv python -m src.analysis.sign_benchmark_multiyear
    uv run --env-file devenv python -m src.analysis.sign_benchmark_multiyear --phase download cluster
    uv run --env-file devenv python -m src.analysis.sign_benchmark_multiyear --phase benchmark --fy FY2024
"""
from __future__ import annotations

import argparse
import datetime
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.cluster import (
    _fiscal_dates,
    _fiscal_label,
    build_distance_matrix,
    cluster_stocks,
    collect_ohlcv,
    run_pair_corr,
    save_clusters,
)
from src.analysis.models import (
    CorrRun,
    N225RegimeSnapshot,
    SignBenchmarkEvent,
    SignBenchmarkRun,
    StockClusterMember,
    StockClusterRun,
)
from src.analysis.regime_ranking import ADX_VETO, build_regime_ranking
from src.analysis.sign_benchmark import run_benchmark
from src.analysis.sign_validate import (
    _binomial_p,
    _build_regime_map,
    _compute_metrics,
    _deduplicate,
    _fmt_p,
    _permutation_test,
)
from src.data.db import get_session
from src.data.nikkei225 import load_or_fetch

# ── Configuration ──────────────────────────────────────────────────────────────

_N225 = "^N225"
_GSPC = "^GSPC"

# (fy_label, bench_start, bench_end, cluster_year)
# cluster_year is the FY used to build the cluster set (FY-1 of the bench year)
FY_CONFIG: list[tuple[str, str, str, str]] = [
    ("FY2018", "2018-04-01", "2019-03-31", "2017"),
    ("FY2019", "2019-04-01", "2020-03-31", "2018"),
    ("FY2020", "2020-04-01", "2021-03-31", "2019"),
    ("FY2021", "2021-04-01", "2022-03-31", "2020"),
    ("FY2022", "2022-04-01", "2023-03-31", "2021"),
    ("FY2023", "2023-04-01", "2024-03-31", "2022"),
    ("FY2024", "2024-04-01", "2025-03-31", "2023"),
    ("FY2025", "2025-04-01", "2026-03-31", "2024"),  # out-of-sample backtest year
]

_TRAINING_FYS = ["FY2018", "FY2019", "FY2020", "FY2021", "FY2022", "FY2023", "FY2024"]

# Cluster years to build (2017–2023; classified2024 needed for FY2025)
_CLUSTER_YEARS_TO_BUILD = ["2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024"]

_DOWNLOAD_START = "2017-04-01"
_DOWNLOAD_END   = "2026-03-31"

# Signs to benchmark (excluding corr_peak and div_bar/div_vol which need special treatment)
SIGNS: list[str] = [
    "div_gap", "div_peer",
    "corr_flip", "corr_shift",
    "str_hold", "str_lead", "str_lag",
    "brk_sma", "brk_bol", "brk_hi_sideway",
    "rev_lo", "rev_hi", "rev_nhi", "rev_nlo", "rev_nhold",
]

_MIN_BARS    = 150   # minimum 1d bars in FY period to include a stock in benchmark
_N_PERMS     = 1000  # permutation iterations (fewer than sign_validate for speed)
_REPORT_PATH = Path("src/analysis/benchmark.md")


# ── Datetime helpers ───────────────────────────────────────────────────────────

def _dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class RunResult:
    fy:          str
    sign:        str
    cluster_set: str
    run_id:      int
    n:           int
    dr:          float
    p_binom:     float
    bench_flw:   float | None
    bench_rev:   float | None
    mean_bars:   float | None
    # Validation
    perm_p:      float        = 0.0
    dedup_n:     int          = 0
    dedup_x:     float        = 1.0
    dedup_dr:    float        = 0.0
    dedup_p:     float        = 1.0
    bear_n:      int          = 0
    bear_dr:     float | None = None
    bear_p:      float | None = None
    bull_n:      int          = 0
    bull_dr:     float | None = None
    bull_p:      float | None = None


# ── Phase 1: Download ──────────────────────────────────────────────────────────

def phase_download(codes: list[str]) -> None:
    """Collect 1d OHLCV for all stocks + ^N225 + ^GSPC from _DOWNLOAD_START to _DOWNLOAD_END."""
    all_codes = codes + [_N225, _GSPC]
    dl_start = _dt(_DOWNLOAD_START)
    dl_end   = _dt(_DOWNLOAD_END) + datetime.timedelta(days=1)  # exclusive
    logger.info("Downloading {} symbols from {} to {} …", len(all_codes), _DOWNLOAD_START, _DOWNLOAD_END)
    with get_session() as session:
        collect_ohlcv(session, all_codes, dl_start, dl_end, "1d")


# ── Phase 2: Cluster ───────────────────────────────────────────────────────────

def _find_corr_run(session: Session, start: datetime.datetime, end: datetime.datetime) -> int | None:
    run = session.execute(
        select(CorrRun)
        .where(CorrRun.start_dt <= start, CorrRun.end_dt >= end)
        .order_by(CorrRun.created_at.desc())
    ).scalars().first()
    return run.id if run else None


def phase_cluster(codes: list[str]) -> None:
    """Build cluster sets for years that don't yet exist in DB."""
    for year in _CLUSTER_YEARS_TO_BUILD:
        label = _fiscal_label(year)
        with get_session() as session:
            existing = session.execute(
                select(StockClusterRun).where(StockClusterRun.fiscal_year == label)
            ).scalar_one_or_none()
        if existing:
            logger.info("{} already in DB (id={}) — skipping", label, existing.id)
            continue

        start, end = _fiscal_dates(year)  # end is exclusive (+1 day)
        logger.info("Building {} for {} → {} …", label, start.date(), (end - datetime.timedelta(days=1)).date())

        with get_session() as session:
            corr_run_id = _find_corr_run(session, start, end)
            if corr_run_id is None:
                logger.info("  Running pair correlation for {} …", year)
                corr_run_id = run_pair_corr(session, codes, start, end)
            else:
                logger.info("  Re-using existing corr_run_id={}", corr_run_id)

            logger.info("  Building distance matrix …")
            condensed, dist_codes = build_distance_matrix(session, corr_run_id)
            clusters = cluster_stocks(condensed, dist_codes)
            save_clusters(session, year, start, end, clusters, corr_run_id, 0.3, "1d")
            logger.info("  {} done: {} clusters from {} stocks", label, len(clusters), len(dist_codes))


# ── Phase 3: Benchmark ─────────────────────────────────────────────────────────

def _count_bars(session: Session, codes: list[str], start: datetime.datetime, end: datetime.datetime) -> dict[str, int]:
    from sqlalchemy import func
    from src.data.models import OHLCV_MODEL_MAP
    model = OHLCV_MODEL_MAP["1d"]
    rows = session.execute(
        select(model.stock_code, func.count().label("n"))
        .where(model.stock_code.in_(codes), model.ts >= start, model.ts < end)
        .group_by(model.stock_code)
    ).all()
    return {r.stock_code: int(r.n) for r in rows}


def _find_existing_run(
    session: Session,
    sign_type: str,
    stock_set: str,
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str = "1d",
) -> SignBenchmarkRun | None:
    return session.execute(
        select(SignBenchmarkRun).where(
            SignBenchmarkRun.sign_type == sign_type,
            SignBenchmarkRun.stock_set == stock_set,
            SignBenchmarkRun.gran == gran,
            SignBenchmarkRun.start_dt == start,
            SignBenchmarkRun.end_dt == end,
        )
    ).scalar_one_or_none()


def phase_benchmark(
    fy_filter: list[str] | None = None,
    sign_filter: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Run benchmarks for all FY × sign combinations. Returns {fy_label: {sign: run_id}}."""
    results: dict[str, dict[str, int]] = {}

    for fy_label, start_str, end_str, cluster_year in FY_CONFIG:
        if fy_filter and fy_label not in fy_filter:
            continue

        cluster_set = _fiscal_label(cluster_year)
        bench_start = _dt(start_str)
        bench_end   = _dt(end_str)

        # Get stock codes from cluster (representatives only)
        with get_session() as session:
            cluster_run = session.execute(
                select(StockClusterRun).where(StockClusterRun.fiscal_year == cluster_set)
            ).scalar_one_or_none()
            if cluster_run is None:
                logger.warning("No cluster run for {} — skipping {}", cluster_set, fy_label)
                continue

            all_codes = list(session.execute(
                select(StockClusterMember.stock_code)
                .where(
                    StockClusterMember.run_id == cluster_run.id,
                    StockClusterMember.is_representative.is_(True),
                )
            ).scalars().all())

        # Filter to stocks with enough bars in the benchmark period
        bench_end_exc = bench_end + datetime.timedelta(days=1)
        with get_session() as session:
            bar_cnts = _count_bars(session, all_codes, bench_start, bench_end_exc)
        codes = [c for c in all_codes if bar_cnts.get(c, 0) >= _MIN_BARS]
        logger.info("{}: {}/{} stocks have >={} 1d bars in {}", fy_label, len(codes), len(all_codes), _MIN_BARS, start_str[:4])

        if len(codes) < 10:
            logger.warning("{}: fewer than 10 qualifying stocks — skipping", fy_label)
            continue

        results[fy_label] = {}
        signs = sign_filter or SIGNS

        for sign in signs:
            with get_session() as session:
                existing = _find_existing_run(session, sign, cluster_set, bench_start, bench_end)
            if existing:
                logger.info("  {}/{} — existing run_id={} (skipping)", fy_label, sign, existing.id)
                results[fy_label][sign] = existing.id
                continue

            logger.info("  {}/{} — running benchmark …", fy_label, sign)
            try:
                with get_session() as session:
                    run_id = run_benchmark(
                        session=session,
                        sign_type=sign,
                        stock_codes=codes,
                        stock_set=cluster_set,
                        start=bench_start,
                        end=bench_end,
                        gran="1d",
                        window=20,
                        valid_bars=5,
                        trend_cap_days=30,
                        zz_size=5,
                        zz_mid_size=2,
                        proximity_pct=0.015,
                        corr_mode="all",
                    )
                results[fy_label][sign] = run_id
                logger.info("  {}/{} — run_id={}", fy_label, sign, run_id)
            except Exception as exc:
                logger.error("  {}/{} FAILED: {}", fy_label, sign, exc)

    return results


# ── Phase 4: Validate ──────────────────────────────────────────────────────────

def phase_validate(bench_results: dict[str, dict[str, int]]) -> list[RunResult]:
    """Run permutation test + regime split for all runs; return structured results."""
    all_run_ids: list[int] = [
        rid
        for fy_runs in bench_results.values()
        for rid in fy_runs.values()
    ]
    if not all_run_ids:
        return []

    regime_start = _dt("2018-01-01")  # broad window for regime map
    regime_end   = _dt("2026-04-01")
    with get_session() as session:
        logger.info("Building N225 regime map …")
        regime_map = _build_regime_map(session, regime_start, regime_end)
        logger.info("  {} dated entries (bear={} bull={})",
                    len(regime_map),
                    sum(1 for v in regime_map.values() if v == "bear"),
                    sum(1 for v in regime_map.values() if v == "bull"))

    run_results: list[RunResult] = []

    for fy_label, fy_runs in bench_results.items():
        # Pull FY meta from config
        cfg = next((c for c in FY_CONFIG if c[0] == fy_label), None)
        if cfg is None:
            continue
        _, start_str, end_str, cluster_year = cfg
        cluster_set = _fiscal_label(cluster_year)

        for sign, run_id in fy_runs.items():
            with get_session() as session:
                run = session.get(SignBenchmarkRun, run_id)
                if run is None:
                    continue
                events = list(session.execute(
                    select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id == run_id)
                ).scalars().all())

            with_trend = [e for e in events if e.trend_direction is not None]
            n = len(with_trend)
            if n == 0:
                run_results.append(RunResult(
                    fy=fy_label, sign=sign, cluster_set=cluster_set, run_id=run_id,
                    n=0, dr=0.0, p_binom=1.0,
                    bench_flw=None, bench_rev=None, mean_bars=None,
                ))
                continue

            directions = [e.trend_direction for e in with_trend]
            dr = sum(1 for d in directions if d == +1) / n
            p_binom = _binomial_p(n, dr)

            # bench metrics
            flw_mags = [e.trend_magnitude for e in with_trend
                        if e.trend_direction == +1 and e.trend_magnitude is not None]
            rev_mags = [e.trend_magnitude for e in with_trend
                        if e.trend_direction == -1 and e.trend_magnitude is not None]
            mag_flw = float(np.mean(flw_mags)) if flw_mags else None
            mag_rev = float(np.mean(rev_mags)) if rev_mags else None
            bench_flw = dr * mag_flw if mag_flw is not None else None
            bench_rev = (1 - dr) * mag_rev if mag_rev is not None else None
            mean_bars = float(np.mean([e.trend_bars for e in with_trend if e.trend_bars is not None]))

            # Permutation test
            perm_p, _, _ = _permutation_test(directions, n_perms=_N_PERMS)

            # Dedup
            dedup_evts = _deduplicate(events)
            dedup_m = _compute_metrics(dedup_evts)
            dedup_n = dedup_m.n if dedup_m else 0
            dedup_x = n / dedup_n if dedup_n > 0 else float("nan")
            dedup_dr = dedup_m.dr if dedup_m else 0.0
            dedup_p  = dedup_m.p_binom if dedup_m else 1.0

            # Regime split
            bear_evts = [e for e in events if regime_map.get(e.fired_at.date()) == "bear"]
            bull_evts = [e for e in events if regime_map.get(e.fired_at.date()) == "bull"]
            bear_m = _compute_metrics(bear_evts)
            bull_m = _compute_metrics(bull_evts)

            rr = RunResult(
                fy=fy_label, sign=sign, cluster_set=cluster_set, run_id=run_id,
                n=n, dr=dr, p_binom=p_binom,
                bench_flw=bench_flw, bench_rev=bench_rev, mean_bars=mean_bars,
                perm_p=perm_p,
                dedup_n=dedup_n, dedup_x=dedup_x, dedup_dr=dedup_dr, dedup_p=dedup_p,
                bear_n=bear_m.n if bear_m else 0,
                bear_dr=bear_m.dr if bear_m else None,
                bear_p=bear_m.p_binom if bear_m else None,
                bull_n=bull_m.n if bull_m else 0,
                bull_dr=bull_m.dr if bull_m else None,
                bull_p=bull_m.p_binom if bull_m else None,
            )
            run_results.append(rr)
            logger.info("  {}/{}: n={} DR={:.1%} p={:.3f} perm_p={:.3f} bear={:.1%}/{:.3f} bull={:.1%}/{:.3f}",
                        fy_label, sign, n, dr, p_binom,
                        perm_p,
                        rr.bear_dr or 0.0, rr.bear_p or 1.0,
                        rr.bull_dr or 0.0, rr.bull_p or 1.0)

    return run_results


# ── Phase 5: Report ────────────────────────────────────────────────────────────

def _pct(v: float | None) -> str:
    return f"{v*100:.1f}%" if v is not None else "—"

def _f3(v: float | None) -> str:
    return f"{v:.3f}" if v is not None else "—"

def _f4(v: float | None) -> str:
    return f"{v:.4f}" if v is not None else "—"

def _p_str(v: float | None) -> str:
    if v is None: return "—"
    if v < 0.001: return "<0.001"
    return f"≈{v:.3f}"


def phase_report(results: list[RunResult]) -> None:
    """Append per-FY + aggregate tables to benchmark.md."""
    today = datetime.date.today().isoformat()
    lines: list[str] = []

    lines += [
        "",
        "---",
        "",
        f"## Multi-Year Benchmark (FY2018–FY2024)",
        "",
        f"Generated: {today}  ",
        f"Universe: Nikkei225 representatives from prior FY's cluster  ",
        f"Granularity: 1d · window=20 · valid_bars=5 · ZZ_SIZE=5 · trend_cap=30  ",
        f"Permutation: {_N_PERMS} iterations  ",
        "",
    ]

    # ── Per-FY tables ──────────────────────────────────────────────────────────
    lines.append("### Per-Fiscal-Year Results")
    lines.append("")

    fy_order = [c[0] for c in FY_CONFIG]
    by_fy: dict[str, list[RunResult]] = {}
    for r in results:
        by_fy.setdefault(r.fy, []).append(r)

    for fy_label in fy_order:
        fy_results = by_fy.get(fy_label)
        if not fy_results:
            continue
        cfg = next(c for c in FY_CONFIG if c[0] == fy_label)
        _, s, e, cy = cfg
        lines.append(f"#### {fy_label} ({s} – {e}) · cluster=classified{cy}")
        lines.append("")
        hdr = ("| Sign | n | DR% | p | bench_flw | bench_rev | mean_bars"
               " | perm_p | dedup_n(×) | dedup_DR | bear_DR (p) | bull_DR (p) |")
        sep = ("|------|---|-----|---|-----------|-----------|----------"
               "|--------|------------|----------|-------------|-------------|")
        lines += [hdr, sep]
        for r in sorted(fy_results, key=lambda x: SIGNS.index(x.sign) if x.sign in SIGNS else 99):
            bear_str = f"{_pct(r.bear_dr)} ({_p_str(r.bear_p)})" if r.bear_dr is not None else "—"
            bull_str = f"{_pct(r.bull_dr)} ({_p_str(r.bull_p)})" if r.bull_dr is not None else "—"
            dedup_str = f"{r.dedup_n} (×{r.dedup_x:.1f})" if r.dedup_x == r.dedup_x else "—"
            lines.append(
                f"| {r.sign:<10} | {r.n:>5} | {_pct(r.dr):>6} | {_p_str(r.p_binom):<7}"
                f" | {_f4(r.bench_flw):>9} | {_f4(r.bench_rev):>9} | {_f3(r.mean_bars):>9}"
                f" | {_p_str(r.perm_p):<7} | {dedup_str:>10}"
                f" | {_pct(r.dedup_dr):>8} | {bear_str:<13} | {bull_str:<13} |"
            )
        lines.append("")

    # ── Aggregate by sign ──────────────────────────────────────────────────────
    lines += ["### Aggregate by Sign (FY2018–FY2024)", ""]

    by_sign: dict[str, list[RunResult]] = {}
    for r in results:
        by_sign.setdefault(r.sign, []).append(r)

    agg_hdr = ("| Sign | FYs | total_n | pooled_DR% | p_pooled | avg_bench_flw"
               " | avg_bench_rev | perm_pass | bear_DR range | bull_DR range |")
    agg_sep = ("|------|-----|---------|------------|----------|--------------|"
               "---------------|-----------|---------------|---------------|")
    lines += [agg_hdr, agg_sep]

    for sign in SIGNS:
        rr = by_sign.get(sign)
        if not rr:
            lines.append(f"| {sign:<10} | — | — | — | — | — | — | — | — | — |")
            continue

        total_n = sum(r.n for r in rr)
        if total_n == 0:
            lines.append(f"| {sign:<10} | {len(rr)} | 0 | — | — | — | — | — | — | — |")
            continue

        # Pooled DR: weighted by n
        pooled_dr = sum(r.dr * r.n for r in rr) / total_n
        p_pooled = _binomial_p(total_n, pooled_dr)

        bf_vals = [r.bench_flw for r in rr if r.bench_flw is not None]
        br_vals = [r.bench_rev for r in rr if r.bench_rev is not None]
        avg_bf = float(np.mean(bf_vals)) if bf_vals else None
        avg_br = float(np.mean(br_vals)) if br_vals else None

        perm_pass = sum(1 for r in rr if r.perm_p < 0.05)
        perm_str  = f"{perm_pass}/{len(rr)}"

        bear_drs = [r.bear_dr for r in rr if r.bear_dr is not None]
        bull_drs = [r.bull_dr for r in rr if r.bull_dr is not None]
        bear_range = (f"{min(bear_drs)*100:.1f}–{max(bear_drs)*100:.1f}%"
                      if len(bear_drs) >= 2 else _pct(bear_drs[0] if bear_drs else None))
        bull_range = (f"{min(bull_drs)*100:.1f}–{max(bull_drs)*100:.1f}%"
                      if len(bull_drs) >= 2 else _pct(bull_drs[0] if bull_drs else None))

        lines.append(
            f"| {sign:<10} | {len(rr):>3} | {total_n:>7} | {_pct(pooled_dr):>10}"
            f" | {_p_str(p_pooled):<8} | {_f4(avg_bf):>12} | {_f4(avg_br):>13}"
            f" | {perm_str:>9} | {bear_range:<13} | {bull_range:<13} |"
        )

    lines += [
        "",
        "**Notes on interpretation**",
        "- pooled_DR% is n-weighted across all FYs; p_pooled is the binomial test on the pooled n.",
        "- perm_pass = FYs where the permutation test passes at p<0.05.",
        "- bear_DR / bull_DR ranges show min–max across FYs.",
        "- Signs consistent across multiple FYs with perm_pass ≥ 4/7 are the most reliable.",
        "",
    ]

    # Write to file
    with open(_REPORT_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logger.info("Appended multi-year results to {}", _REPORT_PATH)


# ── Phase 6: Backtest (FY2025 out-of-sample) ───────────────────────────────────

def phase_backtest(bench_results: dict[str, dict[str, int]]) -> None:
    """Evaluate FY2025 events through FY2018–FY2024 regime ranking.

    Loads training run_ids from DB independently (regardless of what bench_results
    contains for training FYs), so this phase can be run in isolation via
    --phase backtest --fy FY2025.
    """
    # ── Load training run_ids ─────────────────────────────────────────────────
    train_db = _load_bench_results_from_db(fy_filter=_TRAINING_FYS)
    training_run_ids: list[int] = [
        rid for fy_runs in train_db.values() for rid in fy_runs.values()
    ]
    if not training_run_ids:
        logger.error("No FY2018–FY2024 benchmark runs found in DB — cannot build ranking")
        return
    logger.info("Building regime ranking from {} training run_ids …", len(training_run_ids))

    # ── Build regime ranking ──────────────────────────────────────────────────
    with get_session() as session:
        ranking = build_regime_ranking(session, training_run_ids)
    if not ranking:
        logger.error("Regime ranking is empty — ensure n225_regime_snapshots is populated")
        return
    logger.info("Ranking: {} (sign, kumo) cells", len(ranking))

    # ── FY2025 runs ───────────────────────────────────────────────────────────
    fy2025_runs = bench_results.get("FY2025")
    if not fy2025_runs:
        db2025 = _load_bench_results_from_db(fy_filter=["FY2025"])
        fy2025_runs = db2025.get("FY2025", {})
    if not fy2025_runs:
        logger.error("No FY2025 benchmark runs found — run --phase benchmark --fy FY2025 first")
        return
    logger.info("FY2025: {} sign runs to evaluate", len(fy2025_runs))

    # ── Load events + snapshots ───────────────────────────────────────────────
    fy2025_events: dict[str, list[SignBenchmarkEvent]] = {}
    snap_map: dict[datetime.date, N225RegimeSnapshot] = {}
    with get_session() as session:
        for sign, run_id in fy2025_runs.items():
            evts = list(session.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id == run_id)
            ).scalars().all())
            fy2025_events[sign] = evts

        snaps = list(session.execute(
            select(N225RegimeSnapshot).where(
                N225RegimeSnapshot.date >= datetime.date(2025, 4, 1),
                N225RegimeSnapshot.date <= datetime.date(2026, 3, 31),
            )
        ).scalars().all())
        snap_map = {s.date: s for s in snaps}
    logger.info("FY2025 regime snapshots: {} dates", len(snap_map))

    # ── Build report ──────────────────────────────────────────────────────────
    today = datetime.date.today().isoformat()
    lines: list[str] = [
        "",
        "---",
        "",
        "## FY2025 Out-of-Sample Backtest",
        "",
        f"Generated: {today}  ",
        "Training: FY2018–FY2024 regime ranking (Ichimoku Kumo × ADX veto)  ",
        "Test: FY2025 · classified2024 · 2025-04-01 – 2026-03-31  ",
        f"Ranking cells: {len(ranking)} (sign × kumo_state, min_n=30)  ",
        "",
    ]

    # ── Regime-cell detail table ──────────────────────────────────────────────
    lines += [
        "### Regime Cell Detail (sign × kumo_state)",
        "",
        "Kumo states: ▲above cloud (+1) · ~inside (0) · ▼below cloud (−1)  ",
        "Δ DR = test cell DR − sign-level baseline DR (all events for that sign).",
        "",
        "| Sign | kumo | train_bench_flw | train_DR | train_n | test_n | test_DR | Δ DR |",
        "|------|------|-----------------|----------|---------|--------|---------|------|",
    ]

    cell_rows: list[tuple[float, str]] = []

    def _get_date(e: SignBenchmarkEvent) -> datetime.date:
        return e.fired_at.date() if hasattr(e.fired_at, "date") else e.fired_at  # type: ignore[return-value]

    for (sign, kumo), entry in ranking.items():
        all_sign_evts = [e for e in fy2025_events.get(sign, []) if e.trend_direction is not None]
        base_n  = len(all_sign_evts)
        base_dr = sum(1 for e in all_sign_evts if e.trend_direction == +1) / base_n if base_n else None

        cell_evts = [
            e for e in all_sign_evts
            if (s := snap_map.get(_get_date(e))) is not None
            and s.kumo_state is not None
            and int(s.kumo_state) == kumo
        ]
        test_n  = len(cell_evts)
        test_dr = sum(1 for e in cell_evts if e.trend_direction == +1) / test_n if test_n else None
        delta   = (f"{(test_dr - base_dr) * 100:+.1f}%"
                   if test_dr is not None and base_dr is not None else "—")
        kumo_lbl = "▲above" if kumo == 1 else ("~inside" if kumo == 0 else "▼below")
        row = (
            f"| {sign:<10} | {kumo_lbl:<7} | {entry.bench_flw:.4f} | {entry.dr:.1%}"
            f" | {entry.n:>7} | {test_n:>6} | {_pct(test_dr):>7} | {delta:<6} |"
        )
        cell_rows.append((-entry.bench_flw, row))

    cell_rows.sort()
    for _, row in cell_rows:
        lines.append(row)

    # ── Sign summary table ────────────────────────────────────────────────────
    lines += [
        "",
        "### Sign Summary: All Events vs Regime-Accepted Events",
        "",
        "Regime-accepted = (sign, kumo) cell present in training ranking AND ADX veto passes.  ",
        "regime_n% = fraction of total events retained by the regime filter.",
        "",
        "| Sign | total_n | total_DR | regime_n | regime_DR | Δ DR | regime_n% |",
        "|------|---------|----------|----------|-----------|------|-----------|",
    ]

    for sign in SIGNS:
        all_evts = [e for e in fy2025_events.get(sign, []) if e.trend_direction is not None]
        total_n = len(all_evts)
        if total_n == 0:
            lines.append(f"| {sign:<10} | 0 | — | — | — | — | — |")
            continue
        total_dr = sum(1 for e in all_evts if e.trend_direction == +1) / total_n

        regime_evts: list[SignBenchmarkEvent] = []
        for e in all_evts:
            snap = snap_map.get(_get_date(e))
            if snap is None or snap.kumo_state is None:
                continue
            kumo = int(snap.kumo_state)
            if (sign, kumo) not in ranking:
                continue
            req = ADX_VETO.get(sign)
            if req is not None:
                adx  = snap.adx     if snap.adx     is not None else float("nan")
                adxp = snap.adx_pos if snap.adx_pos is not None else float("nan")
                adxn = snap.adx_neg if snap.adx_neg is not None else float("nan")
                if math.isnan(adx) or adx < 20.0:
                    continue
                if req == "bear" and not (adxn > adxp):
                    continue
                if req == "bull" and not (adxp > adxn):
                    continue
            regime_evts.append(e)

        reg_n  = len(regime_evts)
        reg_dr = sum(1 for e in regime_evts if e.trend_direction == +1) / reg_n if reg_n else None
        delta_str   = f"{(reg_dr - total_dr) * 100:+.1f}%" if reg_dr is not None else "—"
        pct_kept    = f"{reg_n / total_n * 100:.0f}%" if total_n else "—"

        lines.append(
            f"| {sign:<10} | {total_n:>7} | {_pct(total_dr):>8} | {reg_n:>8}"
            f" | {_pct(reg_dr):>9} | {delta_str:>6} | {pct_kept:>9} |"
        )

    lines += [
        "",
        "**Interpretation**: Positive Δ DR means the Kumo+ADX regime filter selected",
        "events with better follow-through outcomes in the out-of-sample year.",
        "Low regime_n% indicates the filter is aggressive; verify test_n is large enough.",
        "",
    ]

    with open(_REPORT_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    logger.info("Appended FY2025 backtest results to {}", _REPORT_PATH)


# ── CLI ────────────────────────────────────────────────────────────────────────

_ALL_PHASES = ["download", "cluster", "benchmark", "validate", "report", "backtest"]


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(prog="python -m src.analysis.sign_benchmark_multiyear")
    p.add_argument("--phase", nargs="+", choices=_ALL_PHASES, default=_ALL_PHASES,
                   help="Phases to run (default: all)")
    p.add_argument("--fy", nargs="+", default=None,
                   help="Limit benchmark to specific FYs e.g. FY2021 FY2022")
    p.add_argument("--sign", nargs="+", default=None,
                   help="Limit benchmark to specific signs")
    args = p.parse_args(argv)

    phases = set(args.phase)
    codes = load_or_fetch()
    logger.info("Loaded {} Nikkei225 codes", len(codes))

    if "download" in phases:
        logger.info("=== Phase: download ===")
        phase_download(codes)

    if "cluster" in phases:
        logger.info("=== Phase: cluster ===")
        phase_cluster(codes)

    if "benchmark" in phases:
        logger.info("=== Phase: benchmark ===")
        bench_results = phase_benchmark(
            fy_filter=args.fy,
            sign_filter=args.sign,
        )
    else:
        # Reconstruct bench_results from DB for validate/report
        bench_results = _load_bench_results_from_db(fy_filter=args.fy, sign_filter=args.sign)

    if "validate" in phases:
        logger.info("=== Phase: validate ===")
        run_results = phase_validate(bench_results)
    else:
        run_results = _load_validate_results(bench_results)

    if "report" in phases:
        if not run_results:
            logger.warning("No run results to report.")
        else:
            logger.info("=== Phase: report ===")
            phase_report(run_results)

    if "backtest" in phases:
        logger.info("=== Phase: backtest ===")
        phase_backtest(bench_results)


def _load_bench_results_from_db(
    fy_filter: list[str] | None = None,
    sign_filter: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Reconstruct bench_results by querying existing SignBenchmarkRun rows."""
    results: dict[str, dict[str, int]] = {}
    signs = sign_filter or SIGNS

    with get_session() as session:
        for fy_label, start_str, end_str, cluster_year in FY_CONFIG:
            if fy_filter and fy_label not in fy_filter:
                continue
            cluster_set = _fiscal_label(cluster_year)
            bench_start = _dt(start_str)
            bench_end   = _dt(end_str)
            for sign in signs:
                run = _find_existing_run(session, sign, cluster_set, bench_start, bench_end)
                if run:
                    results.setdefault(fy_label, {})[sign] = run.id

    return results


def _load_validate_results(bench_results: dict[str, dict[str, int]]) -> list[RunResult]:
    """Re-compute validate results from DB without permutation test (for report-only mode)."""
    # For report-only, we compute metrics but skip expensive permutation test
    # Use perm_p=0 as placeholder (not run)
    results: list[RunResult] = []
    for fy_label, fy_runs in bench_results.items():
        cfg = next((c for c in FY_CONFIG if c[0] == fy_label), None)
        if cfg is None:
            continue
        _, start_str, end_str, cluster_year = cfg
        cluster_set = _fiscal_label(cluster_year)

        # Build regime map once per FY
        regime_start = _dt(start_str)
        regime_end   = _dt(end_str) + datetime.timedelta(days=1)

        with get_session() as session:
            regime_map = _build_regime_map(session, regime_start, regime_end)

        for sign, run_id in fy_runs.items():
            with get_session() as session:
                events = list(session.execute(
                    select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id == run_id)
                ).scalars().all())

            with_trend = [e for e in events if e.trend_direction is not None]
            n = len(with_trend)
            if n == 0:
                results.append(RunResult(
                    fy=fy_label, sign=sign, cluster_set=cluster_set, run_id=run_id,
                    n=0, dr=0.0, p_binom=1.0,
                    bench_flw=None, bench_rev=None, mean_bars=None,
                ))
                continue

            directions = [e.trend_direction for e in with_trend]
            dr = sum(1 for d in directions if d == +1) / n
            p_binom = _binomial_p(n, dr)
            flw = [e.trend_magnitude for e in with_trend if e.trend_direction==+1 and e.trend_magnitude]
            rev = [e.trend_magnitude for e in with_trend if e.trend_direction==-1 and e.trend_magnitude]
            mag_flw = float(np.mean(flw)) if flw else None
            mag_rev = float(np.mean(rev)) if rev else None
            mean_bars = float(np.mean([e.trend_bars for e in with_trend if e.trend_bars]))

            dedup_evts = _deduplicate(events)
            dedup_m = _compute_metrics(dedup_evts)
            dedup_n = dedup_m.n if dedup_m else 0

            bear_evts = [e for e in events if regime_map.get(e.fired_at.date()) == "bear"]
            bull_evts = [e for e in events if regime_map.get(e.fired_at.date()) == "bull"]
            bear_m = _compute_metrics(bear_evts)
            bull_m = _compute_metrics(bull_evts)

            results.append(RunResult(
                fy=fy_label, sign=sign, cluster_set=cluster_set, run_id=run_id,
                n=n, dr=dr, p_binom=p_binom,
                bench_flw=dr * mag_flw if mag_flw else None,
                bench_rev=(1-dr) * mag_rev if mag_rev else None,
                mean_bars=mean_bars,
                perm_p=0.0,  # not run in report-only mode
                dedup_n=dedup_n, dedup_x=n/dedup_n if dedup_n else float("nan"),
                dedup_dr=dedup_m.dr if dedup_m else 0.0,
                dedup_p=dedup_m.p_binom if dedup_m else 1.0,
                bear_n=bear_m.n if bear_m else 0,
                bear_dr=bear_m.dr if bear_m else None,
                bear_p=bear_m.p_binom if bear_m else None,
                bull_n=bull_m.n if bull_m else 0,
                bull_dr=bull_m.dr if bull_m else None,
                bull_p=bull_m.p_binom if bull_m else None,
            ))
    return results


if __name__ == "__main__":
    main()
