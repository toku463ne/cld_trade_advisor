"""sign_wait_iv — does waiting K bars after a sign fires preserve the move?

For each multi-year benchmark event:
  - Look up the K-shifted entry price (open of the K-th trading bar after fire).
  - Reconstruct the original peak price from stored
    ``trend_direction × trend_magnitude``.
  - Compute
    ``remaining_signed_return = (peak − entry_K) / entry_K × trend_direction``.

Reports per sign × corr_mode × score_quartile × K:
  - DR(K)         — fraction of events where peak still on favorable side
  - mean_return(K) — average remaining_signed_return

K=0 reproduces the original baseline by construction (peak is always on the
favorable side of the K=0 entry, so DR(0)=1.0 and mean_return(0)=trend_magnitude).
Larger K measures how costly waiting is in terms of move preservation.

This is *not* a re-detection of zigzag from the K-shifted entry (which would
mechanically regress toward 50 % for trending-tape signs as the original edge
is exhausted — see sign-debate cycle critique). It preserves the original target
(the peak the sign was claiming) and asks whether waiting K bars still leaves a
profitable entry against that target.

CLI
---
    uv run --env-file devenv python -m src.analysis.sign_wait_iv
"""

from __future__ import annotations

import bisect
import datetime
import math
import sys
from pathlib import Path
from typing import NamedTuple

import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.analysis.models import SignBenchmarkEvent, SignBenchmarkRun
from src.data.db import get_session
from src.data.models import Ohlcv1d

_BENCH_MD = Path(__file__).parent / "benchmark.md"

_MULTIYEAR_MIN_RUN_ID = 47
_CELL_MIN_N           = 100
_QUARTILE_MIN_N       = 30
_CORR_WINDOW          = 20
_CORR_MIN_PERIODS     = 10
_HIGH_THRESH          = 0.6
_LOW_THRESH           = 0.3
_N225_CODE            = "^N225"
_K_VALUES             = (0, 1, 2, 3, 5, 10)


class _WaitRow(NamedTuple):
    sign:       str
    corr_mode:  str          # "high" | "mid" | "low"
    quartile:   str          # "Q1" | "Q2" | "Q3" | "Q4"
    n:          int
    dr_k:       tuple[float | None, ...]
    mean_ret_k: tuple[float | None, ...]


def _classify_corr(c: float) -> str:
    if c is None or (isinstance(c, float) and math.isnan(c)):
        return "unknown"
    a = abs(c)
    if a >= _HIGH_THRESH:
        return "high"
    if a <= _LOW_THRESH:
        return "low"
    return "mid"


def _load_bars_1d(
    code: str, start: datetime.date, end: datetime.date,
) -> tuple[list[datetime.datetime], list[float], list[float]]:
    """Return (ts_list, open_list, close_list), sorted by ts, dedup'd."""
    start_dt = datetime.datetime.combine(start, datetime.time.min, tzinfo=datetime.timezone.utc)
    end_dt   = datetime.datetime.combine(end,   datetime.time.max, tzinfo=datetime.timezone.utc)
    with get_session() as s:
        rows = s.execute(
            select(Ohlcv1d.ts, Ohlcv1d.open_price, Ohlcv1d.close_price)
            .where(Ohlcv1d.stock_code == code)
            .where(Ohlcv1d.ts >= start_dt)
            .where(Ohlcv1d.ts <= end_dt)
            .order_by(Ohlcv1d.ts)
        ).all()
    ts_list:  list[datetime.datetime] = []
    op_list:  list[float]             = []
    cl_list:  list[float]             = []
    seen: set[datetime.datetime] = set()
    for ts, op, cl in rows:
        if ts in seen:
            continue
        seen.add(ts)
        ts_list.append(ts)
        op_list.append(float(op))
        cl_list.append(float(cl))
    return ts_list, op_list, cl_list


def phase_analyze() -> list[_WaitRow]:
    # 1. Pull multi-year events
    with get_session() as s:
        runs = s.execute(
            select(SignBenchmarkRun).where(SignBenchmarkRun.id >= _MULTIYEAR_MIN_RUN_ID)
        ).scalars().all()
    run_map = {r.id: r.sign_type for r in runs}
    if not run_map:
        logger.warning("No multi-year runs (run_id ≥ {}) found", _MULTIYEAR_MIN_RUN_ID)
        return []

    run_ids = list(run_map)
    rows: list[dict] = []
    for i in range(0, len(run_ids), 500):
        chunk = run_ids[i:i + 500]
        with get_session() as s:
            evts = s.execute(
                select(SignBenchmarkEvent).where(SignBenchmarkEvent.run_id.in_(chunk))
            ).scalars().all()
        for e in evts:
            if e.trend_direction is None or e.trend_magnitude is None:
                continue
            rows.append({
                "sign":     run_map[e.run_id],
                "stock":    e.stock_code,
                "fired_at": e.fired_at,
                "score":    float(e.sign_score),
                "dir":      int(e.trend_direction),
                "mag":      float(e.trend_magnitude),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        logger.warning("No events loaded")
        return []
    logger.info("Loaded {:,} events with non-null outcome", len(df))

    # 2. Bar-load window (extra padding for corr warmup + K=10 lookahead)
    min_d = df["fired_at"].dt.date.min() - datetime.timedelta(days=90)
    max_d = df["fired_at"].dt.date.max() + datetime.timedelta(days=40)

    # 3. ^N225 daily-return series
    n225_ts, _, n225_cl = _load_bars_1d(_N225_CODE, min_d, max_d)
    if not n225_ts:
        logger.error("No ^N225 1d bars in window")
        return []
    n225_close = pd.Series({t.date(): c for t, c in zip(n225_ts, n225_cl)}).sort_index()
    n225_ret   = n225_close.pct_change()

    # 4. Per-stock pass: corr-mode tag + K-shifted return per event
    records: list[dict] = []
    skipped_no_bars = 0
    skipped_no_entry = 0
    for stock, sub in df.groupby("stock"):
        ts_list, op_list, cl_list = _load_bars_1d(stock, min_d, max_d)
        if len(ts_list) < _CORR_WINDOW + 5:
            skipped_no_bars += len(sub)
            continue

        s_close = pd.Series({t.date(): c for t, c in zip(ts_list, cl_list)}).sort_index()
        s_ret   = s_close.pct_change()
        common  = s_ret.index.intersection(n225_ret.index)
        if len(common) < _CORR_WINDOW + 5:
            skipped_no_bars += len(sub)
            continue
        corr = (
            s_ret.reindex(common)
                 .rolling(_CORR_WINDOW, min_periods=_CORR_MIN_PERIODS)
                 .corr(n225_ret.reindex(common))
        )

        for _, e in sub.iterrows():
            fired_at = e["fired_at"]
            # First daily bar STRICTLY after fired_at (matches benchmark logic).
            entry_idx_0 = bisect.bisect_right(ts_list, fired_at)
            if entry_idx_0 >= len(ts_list):
                skipped_no_entry += 1
                continue
            entry_price_0 = op_list[entry_idx_0]
            if entry_price_0 <= 0:
                skipped_no_entry += 1
                continue
            peak_price = entry_price_0 * (1.0 + e["dir"] * e["mag"])
            corr_val   = corr.get(fired_at.date(), float("nan"))
            mode       = _classify_corr(corr_val)
            if mode == "unknown":
                continue

            row: dict = {
                "sign":      e["sign"],
                "corr_mode": mode,
                "score":     e["score"],
                "dir":       e["dir"],
            }
            for K in _K_VALUES:
                idx_k = entry_idx_0 + K
                if idx_k >= len(ts_list):
                    row[f"K{K}"] = None
                    continue
                entry_at_K = op_list[idx_k]
                if entry_at_K <= 0:
                    row[f"K{K}"] = None
                    continue
                row[f"K{K}"] = (peak_price - entry_at_K) / entry_at_K * e["dir"]
            records.append(row)

    if skipped_no_bars:
        logger.info("Skipped {:,} events (no bars / thin corr series)", skipped_no_bars)
    if skipped_no_entry:
        logger.info("Skipped {:,} events (no entry bar after fire)", skipped_no_entry)
    ev_df = pd.DataFrame(records)
    if ev_df.empty:
        return []
    logger.info("Events after corr-mode tagging: {:,}", len(ev_df))

    # 5. Aggregate per (sign × corr_mode × score_quartile)
    out: list[_WaitRow] = []
    for (sign, mode), grp in ev_df.groupby(["sign", "corr_mode"]):
        if len(grp) < _CELL_MIN_N:
            continue
        try:
            grp = grp.copy()
            grp["q"] = pd.qcut(grp["score"], 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
        except ValueError:
            continue
        for ql in ("Q1", "Q2", "Q3", "Q4"):
            qdf = grp[grp["q"] == ql]
            n = len(qdf)
            if n < _QUARTILE_MIN_N:
                continue
            dr_k:  list[float | None] = []
            mr_k:  list[float | None] = []
            for K in _K_VALUES:
                vals = qdf[f"K{K}"].dropna()
                if len(vals) < _QUARTILE_MIN_N:
                    dr_k.append(None)
                    mr_k.append(None)
                    continue
                dr_k.append(float((vals > 0).mean()))
                mr_k.append(float(vals.mean()))
            out.append(_WaitRow(
                sign=sign, corr_mode=mode, quartile=ql, n=n,
                dr_k=tuple(dr_k), mean_ret_k=tuple(mr_k),
            ))

    out.sort(key=lambda r: (r.sign, ["high", "mid", "low"].index(r.corr_mode), r.quartile))
    return out


def _table(rows: list[_WaitRow], values_fn, fmt: str) -> list[str]:
    """Render a (sign, corr, Q) × K table, blanking repeated sign/corr cells."""
    k_hdr = " | ".join(f"K={k}" for k in _K_VALUES)
    out = [
        f"| Sign | corr | Q | n | {k_hdr} |",
        "|------|------|---|---|" + "---|" * len(_K_VALUES),
    ]
    cur_sign: str | None = None
    cur_mode: tuple[str | None, str | None] = (None, None)
    for r in rows:
        sign_cell = f"**{r.sign}**" if r.sign != cur_sign else ""
        mode_cell = r.corr_mode if (r.sign, r.corr_mode) != cur_mode else ""
        cur_sign = r.sign
        cur_mode = (r.sign, r.corr_mode)
        cells = [fmt.format(v) if v is not None else "—" for v in values_fn(r)]
        out.append(
            f"| {sign_cell:<10} | {mode_cell:<4} | {r.quartile} | {r.n:>5} | "
            + " | ".join(cells) + " |"
        )
    return out


def phase_report(rows: list[_WaitRow]) -> None:
    if not rows:
        logger.warning("No rows to report")
        return
    today = datetime.date.today().isoformat()
    md: list[str] = [
        "", "---", "",
        "## Wait-K IV (FY2018–FY2024)",
        "",
        f"Generated: {today}  ",
        "Measures whether waiting K trading bars after a sign fires preserves the move.  ",
        "Per event we reconstruct the original peak price from stored "
        "`trend_direction × trend_magnitude`, look up `Ohlcv1d.open` at the K-shifted "
        "entry bar, and compute "
        "`remaining_signed_return = (peak − entry_K) / entry_K × trend_direction`.  ",
        "At K=0 every event has `remaining > 0` by construction (DR(0)=1.0, "
        "mean_return(0)=trend_magnitude); larger K measures how costly waiting is in "
        "terms of move preservation against the original target.  ",
        f"corr_mode tagged via {_CORR_WINDOW}-bar returns-corr to ^N225 "
        f"(high ≥ {_HIGH_THRESH}, low ≤ {_LOW_THRESH}, mid in between).  ",
        f"Cells with n < {_CELL_MIN_N} dropped; quartile sub-cells with n < "
        f"{_QUARTILE_MIN_N} dropped.  ",
        "",
        "### DR(K) — fraction of events where peak still on favorable side at K-shifted entry",
        "",
    ]
    md += _table(rows, lambda r: r.dr_k, "{:.3f}")
    md += [
        "",
        "### mean_return(K) — average remaining_signed_return per cell",
        "",
    ]
    md += _table(rows, lambda r: r.mean_ret_k, "{:+.4f}")
    md.append("")
    with open(_BENCH_MD, "a", encoding="utf-8") as f:
        f.write("\n".join(md))
    logger.info("Appended Wait-K IV section to {}", _BENCH_MD)


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    rows = phase_analyze()
    phase_report(rows)


if __name__ == "__main__":
    main()
