"""sign_score_decomposition — per-component Spearman ρ for composite sign scores.

Replays a sign detector's component math on existing multi-year benchmark
events so each score component can be correlated separately with the event's
signed_return (= trend_direction × trend_magnitude).

Motivation
----------
`sign_score_calibration` reports the aggregate ρ between `sign_score` and
signed_return per sign. When that aggregate is near zero (or inverted, as for
`str_hold`), we cannot tell which component of a composite score is the
culprit. This module recomputes each component from the underlying 1d OHLCV
bars and reports per-component ρ.

Currently implemented: `str_hold` (three components).

Pipeline
--------
1. Load all multi-year (run_id ≥ _MULTIYEAR_MIN_RUN_ID) SignBenchmarkEvent rows
   for the target sign.
2. Group events by stock_code and batch-load 1d bars for the union of stocks
   and the N225 index across the relevant window.
3. For each event, replay the component formulas as of fire_date.
4. Report per-component Spearman ρ, p-value, n.

CLI
---
    uv run --env-file devenv python -m src.analysis.sign_score_decomposition \
        --sign str_hold
"""

from __future__ import annotations

import argparse
import datetime
import math
import sys
from typing import Callable, NamedTuple

import numpy as np
import pandas as pd
from loguru import logger
from scipy.stats import spearmanr
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.data.models import Ohlcv1d

# Multi-year runs start at this id (mirrors sign_score_calibration)
_MULTIYEAR_MIN_RUN_ID = 47

_N225_CODE = "^N225"


class _EventRow(NamedTuple):
    stock_code:     str
    fire_date:      datetime.date
    components:     dict[str, float]
    signed_return:  float


# ---------------------------------------------------------------------------
# Per-sign component decompositions
# ---------------------------------------------------------------------------
#
# Each entry returns a dict {component_name: value} for one event, or None if
# the inputs are insufficient (e.g., not enough prior bars).
#
# All values must be computed from data strictly at-or-before fire_date — no
# leakage. The formulas mirror the detector module exactly.

ComponentFn = Callable[
    [pd.Series, pd.Series, datetime.date],  # stock daily close, n225 daily close, fire_date
    dict[str, float] | None,
]


# --- str_hold ---------------------------------------------------------------
# Mirror of src/signs/str_hold.py:
#   rel_gap_norm     = min((s5 - n5) / 0.05, 1.0)
#   consistency_sc   = consistent_days / 5
#   n225_depth_bonus = min(|n5| / 0.05, 1.0)
_STR_HOLD_REL_GAP_CAP    = 0.05
_STR_HOLD_N225_DEPTH_CAP = 0.05


def _decompose_str_hold(
    stock_close: pd.Series,
    n225_close:  pd.Series,
    fire_date:   datetime.date,
) -> dict[str, float] | None:
    # Need the fire_date plus 5 prior trading days = 6 closes
    if fire_date not in stock_close.index or fire_date not in n225_close.index:
        return None

    s_idx = stock_close.index.get_loc(fire_date)
    n_idx = n225_close.index.get_loc(fire_date)
    if s_idx < 5 or n_idx < 5:
        return None

    s_window = stock_close.iloc[s_idx - 5 : s_idx + 1]
    n_window = n225_close.iloc[n_idx - 5 : n_idx + 1]

    s5 = float(s_window.iloc[-1] / s_window.iloc[0] - 1.0)
    n5 = float(n_window.iloc[-1] / n_window.iloc[0] - 1.0)

    stock_1d = s_window.pct_change().iloc[1:]   # 5 daily returns
    n225_1d  = n_window.pct_change().iloc[1:]
    consistent_days = int(((stock_1d.values - n225_1d.values) >= 0).sum())

    rel_gap_norm     = min((s5 - n5) / _STR_HOLD_REL_GAP_CAP, 1.0)
    consistency_sc   = consistent_days / 5.0
    n225_depth_bonus = min(abs(n5) / _STR_HOLD_N225_DEPTH_CAP, 1.0)

    return {
        "rel_gap_norm":     float(rel_gap_norm),
        "consistency_sc":   float(consistency_sc),
        "n225_depth_bonus": float(n225_depth_bonus),
    }


_DECOMPOSERS: dict[str, ComponentFn] = {
    "str_hold": _decompose_str_hold,
}


# ---------------------------------------------------------------------------
# Event loading
# ---------------------------------------------------------------------------

def _load_events(sign: str) -> list[tuple[str, datetime.date, float]]:
    """Return (stock_code, fire_date, signed_return) tuples for the sign."""
    with get_session() as session:
        runs = session.execute(
            select(SignBenchmarkRun).where(
                SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID,
                SignBenchmarkRun.sign_type == sign,
            )
        ).scalars().all()
    run_ids = [r.id for r in runs]
    if not run_ids:
        logger.warning("No multi-year runs (id ≥ {}) for sign {!r}", _MULTIYEAR_MIN_RUN_ID, sign)
        return []
    logger.info("Found {} multi-year runs for {} (ids {}–{})",
                len(run_ids), sign, min(run_ids), max(run_ids))

    out: list[tuple[str, datetime.date, float]] = []
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
            signed = int(e.trend_direction) * float(e.trend_magnitude)
            out.append((e.stock_code, e.fired_at.date(), signed))
    logger.info("Loaded {:,} events with non-null outcome", len(out))
    return out


# ---------------------------------------------------------------------------
# Daily close loading (one query per stock; ^N225 once)
# ---------------------------------------------------------------------------

def _load_daily_close(
    stock_code: str,
    start: datetime.datetime,
    end:   datetime.datetime,
) -> pd.Series:
    """Return a date-indexed Series of close prices for stock_code in [start, end)."""
    with get_session() as session:
        rows = session.execute(
            select(Ohlcv1d.ts, Ohlcv1d.close_price).where(
                Ohlcv1d.stock_code == stock_code,
                Ohlcv1d.ts >= start,
                Ohlcv1d.ts <  end,
            ).order_by(Ohlcv1d.ts)
        ).all()
    if not rows:
        return pd.Series(dtype=float)
    dates = [r[0].date() for r in rows]
    closes = [float(r[1]) for r in rows]
    return pd.Series(closes, index=pd.Index(dates), dtype=float)


# ---------------------------------------------------------------------------
# Main: decompose + report
# ---------------------------------------------------------------------------

def run_decomposition(sign: str) -> None:
    if sign not in _DECOMPOSERS:
        logger.error("No decomposer registered for sign {!r}. Available: {}",
                     sign, sorted(_DECOMPOSERS))
        sys.exit(2)
    decompose = _DECOMPOSERS[sign]

    events = _load_events(sign)
    if not events:
        return

    # Window covering all events (with margin for 5-day lookback)
    fire_dates = [d for _, d, _ in events]
    win_start  = datetime.datetime.combine(min(fire_dates), datetime.time()) \
                 - datetime.timedelta(days=20)
    win_end    = datetime.datetime.combine(max(fire_dates), datetime.time()) \
                 + datetime.timedelta(days=2)
    win_start  = win_start.replace(tzinfo=datetime.timezone.utc)
    win_end    = win_end.replace(tzinfo=datetime.timezone.utc)
    logger.info("Bar load window: {} → {}", win_start.date(), win_end.date())

    logger.info("Loading ^N225 daily close …")
    n225_close = _load_daily_close(_N225_CODE, win_start, win_end)
    if n225_close.empty:
        logger.error("No ^N225 bars in window — aborting")
        return
    logger.info("  ^N225: {} daily bars", len(n225_close))

    # Per-stock daily close cache (loaded lazily per stock)
    close_cache: dict[str, pd.Series] = {}

    rows: list[_EventRow] = []
    skipped = 0
    for stock_code, fire_date, signed in events:
        if stock_code not in close_cache:
            close_cache[stock_code] = _load_daily_close(stock_code, win_start, win_end)
        stock_close = close_cache[stock_code]
        if stock_close.empty:
            skipped += 1
            continue
        comps = decompose(stock_close, n225_close, fire_date)
        if comps is None:
            skipped += 1
            continue
        rows.append(_EventRow(
            stock_code=stock_code,
            fire_date=fire_date,
            components=comps,
            signed_return=signed,
        ))

    logger.info("Decomposed {:,} events ({:,} skipped for missing bars or lookback)",
                len(rows), skipped)
    if not rows:
        return

    # Component-by-component Spearman ρ
    component_names = list(rows[0].components.keys())
    signed_arr = np.array([r.signed_return for r in rows])

    print()
    print(f"## Score Decomposition — {sign}")
    print()
    print(f"Events analyzed: {len(rows):,}  (skipped: {skipped:,})")
    print(f"Components in score: {', '.join(component_names)}")
    print()
    print("| Component | min | max | mean | ρ vs signed_return | p | verdict |")
    print("|---|---|---|---|---|---|---|")

    for cname in component_names:
        vals = np.array([r.components[cname] for r in rows])
        rho_res = spearmanr(vals, signed_arr)
        rho = float(rho_res.correlation) if not math.isnan(rho_res.correlation) else None
        pval = float(rho_res.pvalue)      if not math.isnan(rho_res.pvalue)      else None
        verdict = _verdict(rho, pval)
        rho_s = f"{rho:+.4f}" if rho is not None else "—"
        p_s   = (f"<0.001" if pval is not None and pval < 0.001
                 else (f"{pval:.3f}" if pval is not None else "—"))
        print(
            f"| {cname:<18} | {vals.min():.3f} | {vals.max():.3f} | {vals.mean():.3f} | "
            f"{rho_s} | {p_s} | {verdict} |"
        )
    print()

    # Marginal DR per component quartile (so the report parallels sign_score_calibration)
    print("### Per-component quartile DR")
    print()
    print("DR = direction-rate (fraction of events where direction matched expectation).")
    print()
    for cname in component_names:
        vals = np.array([r.components[cname] for r in rows])
        dirs = np.array([1 if r.signed_return > 0 else 0 for r in rows])
        df = pd.DataFrame({"v": vals, "d": dirs})
        try:
            df["q"] = pd.qcut(df["v"], 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
        except ValueError:
            print(f"**{cname}**: insufficient variation for quartiles")
            print()
            continue
        print(f"**{cname}**")
        print()
        print("| Quartile | range | n | DR% |")
        print("|---|---|---|---|")
        for q in ("Q1", "Q2", "Q3", "Q4"):
            qdf = df[df["q"] == q]
            if qdf.empty:
                continue
            print(
                f"| {q} | {qdf['v'].min():.3f}–{qdf['v'].max():.3f} | "
                f"{len(qdf):>5} | {qdf['d'].mean() * 100:.1f}% |"
            )
        print()


def _verdict(rho: float | None, pval: float | None) -> str:
    if rho is None or pval is None:
        return "—"
    if pval >= 0.05:
        return "noise (p≥0.05)"
    if rho > 0:
        return "informative"
    return "**inverted** (ρ<0)"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    p = argparse.ArgumentParser(
        prog="python -m src.analysis.sign_score_decomposition",
        description="Per-component Spearman ρ for composite sign scores.",
    )
    p.add_argument(
        "--sign", required=True, choices=sorted(_DECOMPOSERS),
        help="Sign to decompose.",
    )
    args = p.parse_args(argv)
    run_decomposition(args.sign)


if __name__ == "__main__":
    main()
