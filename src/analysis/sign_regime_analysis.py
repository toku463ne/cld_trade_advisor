"""sign_regime_analysis — ADX + Ichimoku Kumo regime split on sign_benchmark_events.

Pipeline
--------
1. build   : Load ^N225 1d OHLCV, compute ADX(14) and Ichimoku Kumo, upsert into
             n225_regime_snapshots (one row per trading date).
2. analyze : Join sign_benchmark_events with n225_regime_snapshots on fired_at date.
             For each sign, compute direction-rate split by ADX regime and Kumo state.
3. report  : Append markdown tables to src/analysis/benchmark.md.

CLI
---
    # Full pipeline:
    uv run --env-file devenv python -m src.analysis.sign_regime_analysis

    # Build snapshots only:
    uv run --env-file devenv python -m src.analysis.sign_regime_analysis --phase build

    # Analyze + report (snapshots already in DB):
    uv run --env-file devenv python -m src.analysis.sign_regime_analysis --phase analyze report
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
from scipy.stats import binomtest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ta.trend import ADXIndicator

from src.analysis.models import N225RegimeSnapshot, SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP

_BENCH_MD = Path(__file__).parent / "benchmark.md"
_N225 = "^N225"

# ADX
_ADX_WINDOW   = 14
_ADX_CHOPPY   = 20.0   # ADX < this → no clear trend

# Multi-year runs start at this run_id (first run created by sign_benchmark_multiyear)
_MULTIYEAR_MIN_RUN_ID = 47

SIGNS = [
    "div_gap", "div_peer",
    "corr_flip", "corr_shift",
    "str_hold", "str_lead", "str_lag",
    "brk_sma", "brk_bol",
    "rev_lo", "rev_hi", "rev_nhi", "rev_nlo",
]


# ---------------------------------------------------------------------------
# Phase 1: build snapshots
# ---------------------------------------------------------------------------

def phase_build() -> None:
    """Compute ADX + Ichimoku Kumo for ^N225 1d bars and upsert into n225_regime_snapshots."""
    Ohlcv1d = OHLCV_MODEL_MAP["1d"]

    with get_session() as session:
        rows = session.execute(
            select(Ohlcv1d.ts, Ohlcv1d.high_price, Ohlcv1d.low_price, Ohlcv1d.close_price)
            .where(Ohlcv1d.stock_code == _N225)
            .order_by(Ohlcv1d.ts)
        ).all()

    if not rows:
        raise RuntimeError("No ^N225 1d OHLCV rows found in DB. Run data collection first.")

    df = pd.DataFrame(rows, columns=["ts", "high", "low", "close"])
    df["date"] = df["ts"].apply(lambda x: x.date() if hasattr(x, "date") else x)
    df = df.drop_duplicates("date").set_index("date").sort_index()
    logger.info("Loaded {} ^N225 1d bars ({} – {})", len(df), df.index[0], df.index[-1])

    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)

    # ── ADX (Wilder, window=14) via ta library ──────────────────────────────
    adx_ind = ADXIndicator(high=high, low=low, close=close, window=_ADX_WINDOW, fillna=False)
    adx14      = adx_ind.adx()
    plus_di14  = adx_ind.adx_pos()
    minus_di14 = adx_ind.adx_neg()

    # ── Ichimoku Kumo ────────────────────────────────────────────────────────
    tenkan   = (high.rolling(9).max()  + low.rolling(9).min())  / 2
    kijun    = (high.rolling(26).max() + low.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)

    kumo_top    = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    kumo_bottom = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)
    kumo_state  = pd.Series(
        np.where(close > kumo_top, 1, np.where(close < kumo_bottom, -1, 0)),
        index=df.index, dtype=float,
    )
    kumo_state[kumo_top.isna()] = np.nan

    # ── Upsert ───────────────────────────────────────────────────────────────
    snapshot_rows = []
    for date, row in df.iterrows():
        def _f(v: float) -> float | None:
            return None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)

        snapshot_rows.append({
            "date":        date,
            "close":       float(row["close"]),
            "adx":         _f(adx14.get(date)),
            "adx_pos":     _f(plus_di14.get(date)),
            "adx_neg":     _f(minus_di14.get(date)),
            "kumo_top":    _f(kumo_top.get(date)),
            "kumo_bottom": _f(kumo_bottom.get(date)),
            "kumo_state":  None if math.isnan(kumo_state.get(date, float("nan"))) else int(kumo_state[date]),
        })

    with get_session() as session:
        stmt = pg_insert(N225RegimeSnapshot).values(snapshot_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["date"],
            set_={c: stmt.excluded[c] for c in
                  ["close", "adx", "adx_pos", "adx_neg", "kumo_top", "kumo_bottom", "kumo_state"]},
        )
        session.execute(stmt)
        session.commit()

    logger.info("Upserted {} rows into n225_regime_snapshots", len(snapshot_rows))


# ---------------------------------------------------------------------------
# Phase 2: analyze
# ---------------------------------------------------------------------------

class _RegimeRow(NamedTuple):
    sign:    str
    regime:  str
    label:   str
    n:       int
    dr:      float
    p:       float
    vs_all:  float   # overall DR for this sign across all events


def _binom_p(n: int, k: int) -> float:
    if n < 5:
        return float("nan")
    return float(binomtest(k, n, 0.5, alternative="two-sided").pvalue)


def _fmt_p(p: float) -> str:
    if math.isnan(p):
        return "—"
    if p < 0.001:
        return "<0.001"
    return f"≈{p:.3f}"


def phase_analyze() -> tuple[list[_RegimeRow], list[_RegimeRow]]:
    """Return (adx_rows, kumo_rows) — one entry per (sign, regime_state)."""
    # Load snapshots
    with get_session() as session:
        snaps = session.execute(select(N225RegimeSnapshot)).scalars().all()
    snap_map: dict[datetime.date, N225RegimeSnapshot] = {s.date: s for s in snaps}
    logger.info("Loaded {} regime snapshots", len(snap_map))

    # Load multi-year run metadata
    with get_session() as session:
        runs = session.execute(
            select(SignBenchmarkRun).where(SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID)
        ).scalars().all()
    run_map: dict[int, str] = {r.id: r.sign_type for r in runs}
    run_ids = list(run_map)
    logger.info("Found {} multi-year benchmark runs (run_ids {}-{})",
                len(run_ids), min(run_ids), max(run_ids))

    # Load events in batches
    all_events: list[dict] = []
    batch = 500
    for i in range(0, len(run_ids), batch):
        chunk = run_ids[i:i + batch]
        with get_session() as session:
            evts = session.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None:
                continue
            d = e.fired_at.date() if hasattr(e.fired_at, "date") else e.fired_at
            snap = snap_map.get(d)
            all_events.append({
                "sign":        run_map[e.run_id],
                "direction":   e.trend_direction,
                "adx":         snap.adx        if snap else None,
                "adx_pos":     snap.adx_pos    if snap else None,
                "adx_neg":     snap.adx_neg    if snap else None,
                "kumo_state":  snap.kumo_state if snap else None,
            })

    df = pd.DataFrame(all_events)
    logger.info("Loaded {:,} events with outcome", len(df))

    adx_rows:  list[_RegimeRow] = []
    kumo_rows: list[_RegimeRow] = []

    for sign in SIGNS:
        sdf = df[df["sign"] == sign]
        if sdf.empty:
            continue
        all_n = len(sdf)
        all_k = int((sdf["direction"] == 1).sum())
        all_dr = all_k / all_n if all_n else float("nan")

        # ── ADX split ──────────────────────────────────────────────────────
        valid = sdf[sdf["adx"].notna() & sdf["adx_pos"].notna() & sdf["adx_neg"].notna()].copy()
        valid["adx_state"] = "choppy"
        valid.loc[(valid["adx"] >= _ADX_CHOPPY) & (valid["adx_pos"] > valid["adx_neg"]),  "adx_state"] = "bull"
        valid.loc[(valid["adx"] >= _ADX_CHOPPY) & (valid["adx_pos"] <= valid["adx_neg"]), "adx_state"] = "bear"

        for state, label in [("choppy", "choppy (ADX<20)"), ("bull", "bull (ADX≥20,+DI>−DI)"), ("bear", "bear (ADX≥20,+DI≤−DI)")]:
            sub = valid[valid["adx_state"] == state]
            n = len(sub)
            k = int((sub["direction"] == 1).sum())
            dr = k / n if n else float("nan")
            adx_rows.append(_RegimeRow(sign, state, label, n, dr, _binom_p(n, k), all_dr))

        # ── Kumo split ─────────────────────────────────────────────────────
        kdf = sdf[sdf["kumo_state"].notna()]
        for state, label in [(1, "above (+1)"), (0, "inside (0)"), (-1, "below (−1)")]:
            sub = kdf[kdf["kumo_state"] == state]
            n = len(sub)
            k = int((sub["direction"] == 1).sum())
            dr = k / n if n else float("nan")
            kumo_rows.append(_RegimeRow(sign, str(state), label, n, dr, _binom_p(n, k), all_dr))

    return adx_rows, kumo_rows


# ---------------------------------------------------------------------------
# Phase 3: report
# ---------------------------------------------------------------------------

def phase_report(adx_rows: list[_RegimeRow], kumo_rows: list[_RegimeRow]) -> None:
    """Append regime-split tables to benchmark.md."""

    def _table(rows: list[_RegimeRow], regime_col: str) -> str:
        lines = [
            f"| Sign | {regime_col} | n | DR% | p | vs_all |",
            "|------|" + "-" * (len(regime_col) + 2) + "|---|-----|---|--------|",
        ]
        current_sign = None
        for r in rows:
            sep = "| " if r.sign == current_sign else f"| **{r.sign}** "
            current_sign = r.sign
            dr_s   = f"{r.dr * 100:.1f}%" if not math.isnan(r.dr) else "—"
            all_s  = f"{r.vs_all * 100:.1f}%"
            lines.append(
                f"| {r.sign:<10} | {r.label:<25} | {r.n:>6} | {dr_s:>6} | {_fmt_p(r.p):>8} | {all_s:>6} |"
            )
        return "\n".join(lines)

    today = datetime.date.today().isoformat()
    section = f"""
---

## Regime-Split Analysis: ADX + Ichimoku Kumo

Generated: {today}
Indicators computed on ^N225 daily bars.
ADX window=14; Ichimoku: tenkan=9, kijun=26, senkou_b=52 (cloud shift=26).
Events: multi-year runs (FY2018–FY2024, run_ids≥{_MULTIYEAR_MIN_RUN_ID}).
p: two-sided binomial vs H₀=50%.  vs_all: pooled DR for that sign across all regimes.

### ADX Regime Split

ADX regime states:
- **choppy** (ADX < 20): no trending momentum — index oscillating, no directional bias
- **bull** (ADX ≥ 20, +DI > −DI): uptrend with momentum
- **bear** (ADX ≥ 20, +DI ≤ −DI): downtrend with momentum

{_table(adx_rows, "ADX regime")}

### Ichimoku Kumo Regime Split

Kumo state (N225 close vs cloud boundaries at each fired_at date):
- **above (+1)**: close > upper cloud boundary — bullish trend confirmed
- **inside (0)**: close within cloud — transitioning / no clear trend
- **below (−1)**: close < lower cloud boundary — bearish trend confirmed

{_table(kumo_rows, "Kumo")}

"""

    with open(_BENCH_MD, "a", encoding="utf-8") as f:
        f.write(section)

    logger.info("Appended ADX + Kumo regime split to {}", _BENCH_MD)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(prog="python -m src.analysis.sign_regime_analysis")
    p.add_argument(
        "--phase", nargs="+",
        choices=["build", "analyze", "report"],
        default=["build", "analyze", "report"],
        help="Phases to run (default: all)",
    )
    args = p.parse_args(argv)
    phases = set(args.phase)

    if "build" in phases:
        logger.info("=== Phase: build ===")
        phase_build()

    adx_rows: list[_RegimeRow] = []
    kumo_rows: list[_RegimeRow] = []

    if "analyze" in phases:
        logger.info("=== Phase: analyze ===")
        adx_rows, kumo_rows = phase_analyze()

    if "report" in phases:
        if not adx_rows and "analyze" not in phases:
            logger.error("--phase report requires --phase analyze (no results in memory)")
            sys.exit(1)
        logger.info("=== Phase: report ===")
        phase_report(adx_rows, kumo_rows)

    logger.info("Done.")


if __name__ == "__main__":
    main()
