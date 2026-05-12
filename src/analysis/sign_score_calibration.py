"""sign_score_calibration — does the per-event sign_score predict outcomes?

For each sign in the multi-year benchmark, we ask:
  - Is sign_score correlated with the **signed return** (= trend_direction × trend_magnitude)?
  - Within score quartiles, does Q4 (highest scores) have better DR / EV than Q1?

If higher scores reliably correspond to better outcomes the score is informative
and ranking by score is meaningful. If quartiles are flat the score is noise and
the strategy should ignore it (or fire on a uniform 1.0 fallback).

Pipeline
--------
1. analyze : Load all SignBenchmarkEvent rows from multi-year runs (run_id ≥
             _MULTIYEAR_MIN_RUN_ID), compute Spearman ρ between score and
             signed return per sign, and bucket events into score quartiles.
2. report  : Append a "Score Calibration" section to src/analysis/benchmark.md.

CLI
---
    # Full pipeline:
    uv run --env-file devenv python -m src.analysis.sign_score_calibration

    # Analyze only (no markdown write):
    uv run --env-file devenv python -m src.analysis.sign_score_calibration --phase analyze
"""

from __future__ import annotations

import argparse
import datetime
import math
import sys
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
from loguru import logger
from scipy.stats import spearmanr
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session

_BENCH_MD = Path(__file__).parent / "benchmark.md"

# Multi-year runs start at this run_id (mirrors sign_regime_analysis)
_MULTIYEAR_MIN_RUN_ID = 47

# Don't trust per-quartile stats with fewer than this many events
_QUARTILE_MIN_N = 50

SIGNS: list[str] = [
    "div_gap", "div_peer",
    "corr_flip", "corr_shift",
    "str_hold", "str_lead", "str_lag",
    "brk_sma", "brk_bol",
    "rev_lo", "rev_hi", "rev_nhi", "rev_nlo", "rev_nhold",
]


class _SignSummary(NamedTuple):
    sign:    str
    n_total: int
    rho:     float | None    # Spearman ρ between score and signed_return
    p_rho:   float | None    # p-value of ρ
    score_min: float
    score_max: float
    constant_score: bool     # True if score has effectively no variation


class _ScoreRow(NamedTuple):
    sign:        str
    quartile:    str         # "Q1" | "Q2" | "Q3" | "Q4"
    score_range: str         # e.g. "0.123–0.456"
    n:           int
    n_flw:       int
    n_rev:       int
    dr:          float
    mag_flw:     float | None
    mag_rev:     float | None
    ev:          float | None


# ---------------------------------------------------------------------------
# Phase 1: analyze
# ---------------------------------------------------------------------------

def phase_analyze() -> tuple[list[_SignSummary], list[_ScoreRow]]:
    """Return (per-sign summaries, per-quartile rows)."""
    with get_session() as session:
        runs = session.execute(
            select(SignBenchmarkRun).where(SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID)
        ).scalars().all()
    run_map: dict[int, str] = {r.id: r.sign_type for r in runs}
    run_ids = list(run_map)
    if not run_ids:
        logger.warning("No multi-year runs (run_id ≥ {}) found", _MULTIYEAR_MIN_RUN_ID)
        return [], []
    logger.info("Found {} multi-year runs (run_ids {}-{})",
                len(run_ids), min(run_ids), max(run_ids))

    rows: list[dict] = []
    batch = 500
    for i in range(0, len(run_ids), batch):
        chunk = run_ids[i:i + batch]
        with get_session() as session:
            evts = session.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None or e.trend_magnitude is None:
                continue
            rows.append({
                "sign":  run_map[e.run_id],
                "score": float(e.sign_score),
                "dir":   int(e.trend_direction),
                "mag":   float(e.trend_magnitude),
            })

    df = pd.DataFrame(rows)
    logger.info("Loaded {:,} events with non-null score and outcome", len(df))

    summaries: list[_SignSummary] = []
    quartile_rows: list[_ScoreRow] = []

    for sign in SIGNS:
        sdf = df[df["sign"] == sign].copy()
        if sdf.empty:
            continue
        sdf["signed_mag"] = sdf["dir"] * sdf["mag"]

        scores = sdf["score"].to_numpy()
        signed = sdf["signed_mag"].to_numpy()
        score_min = float(scores.min())
        score_max = float(scores.max())
        constant = (score_max - score_min) < 1e-9

        if constant:
            summaries.append(_SignSummary(
                sign=sign, n_total=len(sdf),
                rho=None, p_rho=None,
                score_min=score_min, score_max=score_max,
                constant_score=True,
            ))
            continue

        rho_res = spearmanr(scores, signed)
        rho   = float(rho_res.correlation) if not math.isnan(rho_res.correlation) else None
        p_rho = float(rho_res.pvalue)      if not math.isnan(rho_res.pvalue)      else None
        summaries.append(_SignSummary(
            sign=sign, n_total=len(sdf),
            rho=rho, p_rho=p_rho,
            score_min=score_min, score_max=score_max,
            constant_score=False,
        ))

        # Bucket by score quartile. duplicates="drop" handles ties at quartile cut.
        try:
            sdf["q"] = pd.qcut(
                sdf["score"], 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop",
            )
        except ValueError:
            logger.info("  {}: not enough score variation for quartiles — skipping", sign)
            continue

        for q_label in ("Q1", "Q2", "Q3", "Q4"):
            qdf = sdf[sdf["q"] == q_label]
            n   = len(qdf)
            if n == 0:
                continue
            n_flw = int((qdf["dir"] == 1).sum())
            n_rev = n - n_flw
            dr    = n_flw / n
            mag_flw = (
                float(qdf.loc[qdf["dir"] ==  1, "mag"].mean()) if n_flw > 0 else None
            )
            mag_rev = (
                float(qdf.loc[qdf["dir"] == -1, "mag"].mean()) if n_rev > 0 else None
            )
            if mag_flw is not None and mag_rev is not None:
                ev = dr * mag_flw - (1.0 - dr) * mag_rev
            elif mag_flw is not None and n_rev == 0:
                ev = mag_flw
            elif mag_rev is not None and n_flw == 0:
                ev = -mag_rev
            else:
                ev = None
            quartile_rows.append(_ScoreRow(
                sign=sign, quartile=q_label,
                score_range=f"{qdf['score'].min():.3f}–{qdf['score'].max():.3f}",
                n=n, n_flw=n_flw, n_rev=n_rev,
                dr=dr, mag_flw=mag_flw, mag_rev=mag_rev, ev=ev,
            ))

    return summaries, quartile_rows


# ---------------------------------------------------------------------------
# Phase 2: report
# ---------------------------------------------------------------------------

def _fmt_p(p: float | None) -> str:
    if p is None or math.isnan(p):
        return "—"
    if p < 0.001:
        return "<0.001"
    return f"≈{p:.3f}"


def _verdict(summary: _SignSummary) -> str:
    if summary.constant_score:
        return "n/a (constant)"
    if summary.rho is None or summary.p_rho is None:
        return "—"
    if summary.p_rho >= 0.05:
        return "noise (p≥0.05)"
    if summary.rho > 0:
        return "informative"
    return "**inverted** (ρ<0)"


def phase_report(summaries: list[_SignSummary], rows: list[_ScoreRow]) -> None:
    today = datetime.date.today().isoformat()

    out: list[str] = ["", "---", "", "## Score Calibration: Does sign_score Predict Outcomes?", ""]
    out += [
        f"Generated: {today}  ",
        f"Events: multi-year runs (FY2018–FY2024, run_ids ≥ {_MULTIYEAR_MIN_RUN_ID}).  ",
        "signed_return = trend_direction × trend_magnitude (+ when sign follows, − when reverses).  ",
        "ρ: Spearman correlation between sign_score and signed_return.  ",
        f"Per-quartile rows with n < {_QUARTILE_MIN_N} are shown but their stats are masked.  ",
        "",
        "### Summary",
        "",
        "| Sign | n | score range | ρ | p(ρ) | verdict |",
        "|------|---|-------------|---|------|---------|",
    ]
    for s in summaries:
        rho_s = f"{s.rho:+.3f}" if s.rho is not None else "—"
        out.append(
            f"| {s.sign:<10} | {s.n_total:>6} | "
            f"{s.score_min:.3f}–{s.score_max:.3f} | "
            f"{rho_s:>7} | {_fmt_p(s.p_rho):>7} | {_verdict(s)} |"
        )

    out += [
        "",
        "### Quartile Breakdown",
        "",
        "DR = direction-rate; mag_flw / mag_rev = mean trend_magnitude when the trend follows / reverses;",
        "EV = DR × mag_flw − (1−DR) × mag_rev (expected return per trade in that quartile).",
        "If the score is informative we expect EV(Q4) ≫ EV(Q1).",
        "",
        "| Sign | Quartile | score range | n | DR% | mag_flw | mag_rev | EV |",
        "|------|----------|-------------|---|-----|---------|---------|----|",
    ]
    current_sign: str | None = None
    for r in rows:
        sign_cell = f"**{r.sign}**" if r.sign != current_sign else ""
        current_sign = r.sign
        if r.n < _QUARTILE_MIN_N:
            out.append(
                f"| {sign_cell:<14} | {r.quartile} | {r.score_range:<13} | "
                f"{r.n:>5} | — | — | — | — |"
            )
            continue
        dr_s   = f"{r.dr * 100:.1f}%"
        flw_s  = f"{r.mag_flw:.4f}" if r.mag_flw is not None else "—"
        rev_s  = f"{r.mag_rev:.4f}" if r.mag_rev is not None else "—"
        ev_s   = f"{r.ev:+.4f}"     if r.ev      is not None else "—"
        out.append(
            f"| {sign_cell:<14} | {r.quartile} | {r.score_range:<13} | "
            f"{r.n:>5} | {dr_s:>5} | {flw_s:>7} | {rev_s:>7} | {ev_s:>8} |"
        )
    out.append("")

    with open(_BENCH_MD, "a", encoding="utf-8") as f:
        f.write("\n".join(out))
    logger.info("Appended score calibration section to {}", _BENCH_MD)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(
        prog="python -m src.analysis.sign_score_calibration",
        description="Calibrate per-event sign_score against trade outcomes.",
    )
    p.add_argument(
        "--phase", nargs="+", default=["analyze", "report"],
        choices=["analyze", "report"],
        help="Phases to run (default: analyze report).",
    )
    args = p.parse_args(argv)

    summaries: list[_SignSummary] = []
    rows: list[_ScoreRow] = []
    if "analyze" in args.phase:
        summaries, rows = phase_analyze()
    if "report" in args.phase:
        if not summaries and not rows:
            # Allow report-only by re-running analyze.
            summaries, rows = phase_analyze()
        phase_report(summaries, rows)


if __name__ == "__main__":
    main()
