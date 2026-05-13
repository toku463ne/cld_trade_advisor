"""sign_score_calibration — does the per-event sign_score predict outcomes?

For each sign in the multi-year benchmark, we ask:
  - Is sign_score correlated with the **signed return** (= trend_direction × trend_magnitude)?
  - Within score quartiles, does Q4 (highest scores) have better DR / EV than Q1?

If higher scores reliably correspond to better outcomes the score is informative
and ranking by score is meaningful. If quartiles are flat the score is noise and
the strategy should ignore it (or fire on a uniform 1.0 fallback).

With `--by-regime` an additional pass splits each sign by `corr_mode`
(high / mid / low 20-bar returns-corr to ^N225) and reports per-cell ρ,
BH-FDR q-value across all eligible cells, FY-leave-one-out stability, and
quartile EV monotonicity. This addresses pooling that hides regime-conditional
informativeness (see sign-debate cycle 2026-05-12).

Pipeline
--------
1. analyze : Load all SignBenchmarkEvent rows from multi-year runs (run_id ≥
             _MULTIYEAR_MIN_RUN_ID), compute Spearman ρ between score and
             signed return per sign, and bucket events into score quartiles.
2. report  : Append a "Score Calibration" section to src/analysis/benchmark.md.
3. by-regime: Optional. Append a "Sign Score Calibration by Regime" section
             with per-(sign, corr_mode) ρ, q, FY-LOO, and quartile EV.

CLI
---
    # Full pipeline (pooled only):
    uv run --env-file devenv python -m src.analysis.sign_score_calibration

    # With per-regime breakdown:
    uv run --env-file devenv python -m src.analysis.sign_score_calibration --by-regime

    # Analyze only (no markdown write):
    uv run --env-file devenv python -m src.analysis.sign_score_calibration --phase analyze
"""

from __future__ import annotations

import argparse
import datetime
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
from loguru import logger
from scipy.stats import spearmanr
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.data.models import Ohlcv1d

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
# Phase 3 (optional): per-(sign, corr_mode) calibration
# ---------------------------------------------------------------------------

_REGIME_MIN_N          = 200    # min events per (sign, corr_mode) cell
_REGIME_QUARTILE_MIN_N = 30     # min events per quartile within a cell
_CORR_WINDOW           = 20
_HIGH_THRESH           = 0.6
_LOW_THRESH            = 0.3
_N225_CODE             = "^N225"


class _RegimeRow(NamedTuple):
    sign:        str
    corr_mode:   str             # "high" | "mid" | "low"
    n:           int
    rho:         float | None
    p_rho:       float | None
    q_rho:       float | None    # BH-FDR q-value across cells
    rho_loo_min: float | None    # FY-leave-one-out worst ρ
    rho_loo_max: float | None    # FY-leave-one-out best ρ
    flip_count:  int             # FYs where LOO ρ has opposite sign to full
    monotone:    str             # "asc" | "desc" | "no"
    q_evs:       tuple[float | None, float | None, float | None, float | None]
    q_ns:        tuple[int, int, int, int]


def _jp_fy(d: datetime.date) -> int:
    """Japan fiscal year: Apr 1 → Mar 31. FY2018 = 2018-04-01..2019-03-31."""
    return d.year if d.month >= 4 else d.year - 1


def _bh_fdr(pvals: list[float]) -> list[float]:
    """Benjamini–Hochberg FDR-adjusted q-values."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    q = [1.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        val = min(1.0, pvals[i] * m / rank)
        prev = min(prev, val)
        q[i] = prev
    return q


def _classify_corr(c: float) -> str:
    if math.isnan(c):
        return "unknown"
    a = abs(c)
    if a >= _HIGH_THRESH:
        return "high"
    if a <= _LOW_THRESH:
        return "low"
    return "mid"


def _load_closes(stock_code: str, start: datetime.date, end: datetime.date) -> pd.Series:
    start_dt = datetime.datetime.combine(start, datetime.time.min, tzinfo=datetime.timezone.utc)
    end_dt   = datetime.datetime.combine(end,   datetime.time.max, tzinfo=datetime.timezone.utc)
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv1d.ts, Ohlcv1d.close_price)
            .where(Ohlcv1d.stock_code == stock_code)
            .where(Ohlcv1d.ts >= start_dt)
            .where(Ohlcv1d.ts <= end_dt)
            .order_by(Ohlcv1d.ts)
        ).all()
    if not rows:
        return pd.Series(dtype=float)
    ser = pd.Series([float(r[1]) for r in rows], index=[r[0].date() for r in rows], dtype=float)
    return ser[~ser.index.duplicated(keep="last")]


def phase_analyze_by_regime() -> list[_RegimeRow]:
    """Per-(sign, corr_mode) ρ, q-value, FY-LOO, quartile EV. Returns sorted rows."""
    with get_session() as session:
        runs = session.execute(
            select(SignBenchmarkRun).where(SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID)
        ).scalars().all()
    run_map = {r.id: r.sign_type for r in runs}
    if not run_map:
        return []

    rows: list[dict] = []
    run_ids = list(run_map)
    for i in range(0, len(run_ids), 500):
        chunk = run_ids[i:i + 500]
        with get_session() as session:
            evts = session.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None or e.trend_magnitude is None:
                continue
            rows.append({
                "sign":  run_map[e.run_id],
                "stock": e.stock_code,
                "date":  e.fired_at.date(),
                "score": float(e.sign_score),
                "dir":   int(e.trend_direction),
                "mag":   float(e.trend_magnitude),
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return []
    df["signed_mag"] = df["dir"] * df["mag"]
    df["fy"] = df["date"].apply(_jp_fy)
    logger.info("Regime calibration: {:,} events", len(df))

    # Tag corr_mode per event via 20-bar returns-corr to ^N225 replay.
    min_d = df["date"].min() - datetime.timedelta(days=90)
    max_d = df["date"].max() + datetime.timedelta(days=2)
    n225 = _load_closes(_N225_CODE, min_d, max_d)
    if n225.empty:
        logger.error("No ^N225 1d bars available")
        return []
    n225_ret = n225.pct_change()

    modes = pd.Series("unknown", index=df.index, dtype=object)
    for stock, sub in df.groupby("stock"):
        closes = _load_closes(stock, min_d, max_d)
        if closes.empty:
            continue
        ret = closes.pct_change()
        common = ret.index.intersection(n225_ret.index)
        if len(common) < _CORR_WINDOW + 5:
            continue
        roll = ret.reindex(common).rolling(_CORR_WINDOW).corr(n225_ret.reindex(common))
        for idx, d in zip(sub.index, sub["date"]):
            c = roll.get(d, float("nan"))
            modes[idx] = _classify_corr(c)
    df["corr_mode"] = modes
    df = df[df["corr_mode"].isin(("high", "mid", "low"))]
    logger.info("After corr_mode tagging: {:,} events", len(df))

    # Compute per-cell ρ + FY-LOO + quartile EV. Collect p-values for BH-FDR.
    raw: list[dict] = []
    for (sign, mode), grp in df.groupby(["sign", "corr_mode"]):
        n = len(grp)
        if n < _REGIME_MIN_N:
            continue
        scores = grp["score"].to_numpy()
        signed = grp["signed_mag"].to_numpy()
        if scores.std() < 1e-9:
            continue
        rr = spearmanr(scores, signed)
        rho = float(rr.correlation) if not math.isnan(rr.correlation) else None
        p   = float(rr.pvalue)      if not math.isnan(rr.pvalue)      else None
        if rho is None or p is None:
            continue

        loo_rhos: list[float] = []
        for fy in sorted(grp["fy"].unique()):
            sub = grp[grp["fy"] != fy]
            if len(sub) < 50 or sub["score"].std() < 1e-9:
                continue
            rs = spearmanr(sub["score"], sub["signed_mag"]).correlation
            if not math.isnan(rs):
                loo_rhos.append(float(rs))
        flip = sum(1 for r in loo_rhos if (r > 0) != (rho > 0))

        # Quartile EV
        try:
            grp = grp.copy()
            grp["q"] = pd.qcut(grp["score"], 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
        except ValueError:
            q_evs: list[float | None] = [None, None, None, None]
            q_ns:  list[int]          = [0, 0, 0, 0]
            mono = "no"
        else:
            q_evs = []
            q_ns  = []
            for ql in ("Q1", "Q2", "Q3", "Q4"):
                qdf = grp[grp["q"] == ql]
                qn = len(qdf)
                q_ns.append(qn)
                if qn < _REGIME_QUARTILE_MIN_N:
                    q_evs.append(None)
                    continue
                nf = int((qdf["dir"] == 1).sum())
                nr = qn - nf
                dr = nf / qn
                mf = float(qdf.loc[qdf["dir"] ==  1, "mag"].mean()) if nf else 0.0
                mv = float(qdf.loc[qdf["dir"] == -1, "mag"].mean()) if nr else 0.0
                q_evs.append(dr * mf - (1 - dr) * mv)
            evs = [e for e in q_evs if e is not None]
            if len(evs) == 4:
                asc  = all(q_evs[i] <= q_evs[i + 1] for i in range(3))
                desc = all(q_evs[i] >= q_evs[i + 1] for i in range(3))
                mono = "asc" if asc else ("desc" if desc else "no")
            else:
                mono = "no"

        raw.append({
            "sign": sign, "mode": mode, "n": n,
            "rho": rho, "p": p,
            "loo_min": min(loo_rhos) if loo_rhos else None,
            "loo_max": max(loo_rhos) if loo_rhos else None,
            "flips": flip, "monotone": mono,
            "q_evs": tuple(q_evs), "q_ns": tuple(q_ns),
        })

    if not raw:
        logger.warning("No (sign, corr_mode) cell met n ≥ {} — empty regime table.", _REGIME_MIN_N)
        return []

    qvals = _bh_fdr([r["p"] for r in raw])
    out = [_RegimeRow(
        sign=r["sign"], corr_mode=r["mode"], n=r["n"],
        rho=r["rho"], p_rho=r["p"], q_rho=q,
        rho_loo_min=r["loo_min"], rho_loo_max=r["loo_max"],
        flip_count=r["flips"], monotone=r["monotone"],
        q_evs=r["q_evs"], q_ns=r["q_ns"],
    ) for r, q in zip(raw, qvals)]
    out.sort(key=lambda x: (x.sign, ["high", "mid", "low"].index(x.corr_mode)))
    return out


def _regime_verdict(r: _RegimeRow) -> str:
    if r.q_rho is None or r.p_rho is None or r.rho is None:
        return "—"
    big = abs(r.rho) >= 0.10
    mid = abs(r.rho) >= 0.05
    q_ok = r.q_rho < 0.05
    p_ok = r.p_rho < 0.05
    p_strict = r.p_rho < 0.01
    mono_ok = r.monotone != "no"
    stable = r.flip_count == 0
    if r.n >= 1000 and mid and p_ok and q_ok and mono_ok and stable:
        return "**strong**"
    if r.n >= _REGIME_MIN_N and big and p_strict and q_ok and mono_ok and stable:
        return "moderate"
    if mid and p_ok:
        return "borderline"
    return "noise"


def phase_report_by_regime(rows: list[_RegimeRow]) -> None:
    today = datetime.date.today().isoformat()
    out: list[str] = ["", "---", "", "## Sign Score Calibration by Regime", ""]
    out += [
        f"Generated: {today}  ",
        f"Events: multi-year runs (FY2018–FY2024, run_ids ≥ {_MULTIYEAR_MIN_RUN_ID}).  ",
        f"corr_mode tagged per event via {_CORR_WINDOW}-bar returns-corr to ^N225 "
        f"(high ≥ {_HIGH_THRESH}, low ≤ {_LOW_THRESH}, mid in between).  ",
        f"Only (sign, corr_mode) cells with n ≥ {_REGIME_MIN_N} are tabulated.  ",
        "q = Benjamini–Hochberg FDR across listed cells.  ",
        "ρ_loo_min / ρ_loo_max: ρ recomputed leaving one FY out, worst / best.  ",
        "flips: FYs where leave-one-out ρ has the opposite sign vs full-sample ρ.  ",
        "monotone: quartile EV ordering (asc = Q1<Q2<Q3<Q4, desc = reverse, no = neither).  ",
        "Verdict gates: strong = n≥1000 ∧ |ρ|≥0.05 ∧ p<0.05 ∧ q<0.05 ∧ monotone ∧ 0 flips;  ",
        "moderate = n≥200 ∧ |ρ|≥0.10 ∧ p<0.01 ∧ q<0.05 ∧ monotone ∧ 0 flips.  ",
        "",
        "### Per-cell summary",
        "",
        "| Sign | corr | n | ρ | p | q | ρ_loo_min | ρ_loo_max | flips | mono | verdict |",
        "|------|------|---|---|---|---|-----------|-----------|-------|------|---------|",
    ]
    cur = None
    for r in rows:
        sign_cell = f"**{r.sign}**" if r.sign != cur else ""
        cur = r.sign
        out.append(
            f"| {sign_cell:<10} | {r.corr_mode:<4} | {r.n:>5} | "
            f"{r.rho:+.3f} | {_fmt_p(r.p_rho):>7} | "
            f"{r.q_rho:.3f} | "
            f"{r.rho_loo_min:+.3f} | {r.rho_loo_max:+.3f} | "
            f"{r.flip_count} | {r.monotone} | {_regime_verdict(r)} |"
        )

    out += [
        "",
        "### Quartile EV by cell",
        "",
        "EV = DR × mag_flw − (1−DR) × mag_rev. Quartile cells with n < "
        f"{_REGIME_QUARTILE_MIN_N} are masked.  ",
        "",
        "| Sign | corr | Q1 EV (n) | Q2 EV (n) | Q3 EV (n) | Q4 EV (n) |",
        "|------|------|-----------|-----------|-----------|-----------|",
    ]
    cur = None
    for r in rows:
        sign_cell = f"**{r.sign}**" if r.sign != cur else ""
        cur = r.sign
        cells = []
        for ev, n in zip(r.q_evs, r.q_ns):
            cells.append(f"{ev:+.4f} ({n})" if ev is not None else f"— ({n})")
        out.append(
            f"| {sign_cell:<10} | {r.corr_mode:<4} | "
            f"{cells[0]:<10} | {cells[1]:<10} | {cells[2]:<10} | {cells[3]:<10} |"
        )
    out.append("")

    with open(_BENCH_MD, "a", encoding="utf-8") as f:
        f.write("\n".join(out))
    logger.info("Appended regime calibration section to {}", _BENCH_MD)


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
    p.add_argument(
        "--by-regime", action="store_true",
        help="Also append per-(sign, corr_mode) calibration with BH-FDR + FY-LOO.",
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

    if args.by_regime:
        regime_rows = phase_analyze_by_regime()
        if regime_rows:
            phase_report_by_regime(regime_rows)


if __name__ == "__main__":
    main()
