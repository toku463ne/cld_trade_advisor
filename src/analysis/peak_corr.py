"""Peak-correlation analysis.

For each major-index zigzag peak, compute two correlation metrics with
every stock in a given set:

  A  — 20-bar return correlation ending *at* the peak day
  B  — 5-bar return correlation starting 3 bars *after* the peak day

mean_corr_a / mean_corr_b are the averages across all confirmed peaks
(direction ±2) of that indicator.

CLI
---
    uv run --env-file devenv python -m src.analysis.peak_corr \\
        --stock-set medium --start 2022-01-01 --end 2025-12-31
"""

from __future__ import annotations

import argparse
import datetime
import sys
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.analysis.models import PeakCorrResult, PeakCorrRun
from src.indicators.zigzag import detect_peaks
from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP, Stock
from src.data.models import Stock  # noqa: F811 — already imported once

MAJOR_INDICATORS: list[str] = [
    "^N225", "^DJI", "^GSPC", "^IXIC", "^HSI", "^GDAXI", "^FTSE", "^VIX",
]

# ── Data loading ──────────────────────────────────────────────────────────────


def _load_field(
    session: Session,
    codes: list[str],
    field: str,          # "close_price" | "high_price" | "low_price"
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str,
) -> pd.DataFrame:
    """Load one OHLCV field for *codes*, returning a date-indexed DataFrame.

    Timestamps are normalised to their calendar date so JP, US and EU bars
    all land on the same rows despite different stored times-of-day.
    """
    model = OHLCV_MODEL_MAP[gran]
    col   = getattr(model, field)
    data: dict[str, dict[datetime.date, float]] = {}

    for code in codes:
        rows = session.execute(
            select(model.ts, col)
            .where(model.stock_code == code, model.ts >= start, model.ts < end)
            .order_by(model.ts)
        ).all()
        if rows:
            data[code] = {r.ts.date(): r[1] for r in rows}

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    df.index = pd.DatetimeIndex(df.index)
    df.sort_index(inplace=True)
    return df


# ── Core computation ──────────────────────────────────────────────────────────


def compute(
    session: Session,
    stock_codes: list[str],
    indicators: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str = "1d",
    zz_size: int = 5,
    zz_middle_size: int = 2,
) -> list[dict[str, Any]]:
    """Return list of result dicts (stock, indicator, mean_corr_a, mean_corr_b, n_peaks)."""

    all_codes = list(dict.fromkeys(indicators + stock_codes))  # preserve order, dedup

    logger.info("Loading close prices for {} codes …", len(all_codes))
    df_close = _load_field(session, all_codes, "close_price", start, end, gran)
    if df_close.empty:
        raise RuntimeError("No close price data found.")

    # High / low only needed for the indicators
    avail_inds = [c for c in indicators if c in df_close.columns]
    logger.info("Loading high/low for {} indicators …", len(avail_inds))
    df_high = _load_field(session, avail_inds, "high_price", start, end, gran)
    df_low  = _load_field(session, avail_inds, "low_price",  start, end, gran)

    df_returns = df_close.pct_change()
    avail_stocks = [c for c in stock_codes if c in df_returns.columns and c not in avail_inds]

    logger.info("Detecting peaks for {} indicators, correlating with {} stocks …",
                len(avail_inds), len(avail_stocks))

    results: list[dict[str, Any]] = []

    for ind in avail_inds:
        # Build continuous (no-NaN) series for zigzag detection
        ind_close = df_close[ind].dropna()
        if ind not in df_high.columns:
            continue
        ind_high_s = df_high[ind].reindex(ind_close.index).ffill()
        ind_low_s  = df_low[ind].reindex(ind_close.index).ffill()

        peaks = detect_peaks(
            ind_high_s.tolist(), ind_low_s.tolist(),
            size=zz_size, middle_size=zz_middle_size,
        )
        confirmed = [p for p in peaks if abs(p.direction) == 2]
        if not confirmed:
            logger.warning("No confirmed peaks for {}", ind)
            continue

        logger.info("  {} → {} confirmed peaks", ind, len(confirmed))

        # Map local indices (in ind_close) back to union df_returns positions
        ind_dates = ind_close.index  # DatetimeIndex of the indicator's trading days
        union_dates = df_returns.index

        for stock in avail_stocks:
            a_vals: list[float] = []
            b_vals: list[float] = []

            for peak in confirmed:
                peak_date = ind_dates[peak.bar_index]
                try:
                    pos = union_dates.get_loc(peak_date)
                except KeyError:
                    continue

                # A: 20-bar window ending at peak (inclusive)
                if pos >= 20:
                    sl = df_returns.iloc[pos - 20 : pos + 1][[ind, stock]].dropna()
                    if len(sl) >= 10:
                        r = sl[ind].corr(sl[stock])
                        if pd.notna(r):
                            a_vals.append(float(r))

                # B: 5-bar window starting 3 bars after peak
                b0, b1 = pos + 3, pos + 8
                if b1 <= len(df_returns):
                    sl = df_returns.iloc[b0:b1][[ind, stock]].dropna()
                    if len(sl) >= 3:
                        r = sl[ind].corr(sl[stock])
                        if pd.notna(r):
                            b_vals.append(float(r))

            results.append({
                "stock":       stock,
                "indicator":   ind,
                "mean_corr_a": float(np.mean(a_vals)) if a_vals else None,
                "mean_corr_b": float(np.mean(b_vals)) if b_vals else None,
                "n_peaks":     len(a_vals),
            })

    return results


# ── DB persistence ────────────────────────────────────────────────────────────


def run_and_save(
    session: Session,
    stock_codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str = "1d",
    zz_size: int = 5,
    zz_middle_size: int = 2,
    stock_set: str | None = None,
    indicators: list[str] | None = None,
) -> int:
    """Compute and persist.  Returns new peak_corr_run.id."""
    inds = indicators or MAJOR_INDICATORS
    rows = compute(session, stock_codes, inds, start, end, gran, zz_size, zz_middle_size)

    unique_stocks = len({r["stock"] for r in rows})
    unique_inds   = len({r["indicator"] for r in rows})

    run = PeakCorrRun(
        created_at     = datetime.datetime.now(datetime.timezone.utc),
        start_dt       = start,
        end_dt         = end,
        granularity    = gran,
        zz_size        = zz_size,
        zz_middle_size = zz_middle_size,
        stock_set      = stock_set,
        n_indicators   = unique_inds,
        n_stocks       = unique_stocks,
    )
    session.add(run)
    session.flush()

    session.execute(
        pg_insert(PeakCorrResult),
        [{"run_id": run.id, **r} for r in rows],
    )
    session.commit()
    logger.info("Saved {} results → peak_corr_run_id={}", len(rows), run.id)
    return run.id


# ── Stock name upsert (reuse same pattern as stock_corrs.py) ──────────────────


def _upsert_names(session: Session, codes: list[str]) -> None:
    import yfinance as yf
    existing = {r.code for r in session.execute(select(Stock.code).where(Stock.code.in_(codes))).all()}
    missing  = [c for c in codes if c not in existing]
    if not missing:
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = []
    for code in missing:
        try:
            info = yf.Ticker(code).info
            name = info.get("shortName") or info.get("longName") or code
        except Exception:
            name = code
        rows.append({"code": code, "name": name, "updated_at": now})
    stmt = pg_insert(Stock).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={"name": stmt.excluded.name, "updated_at": stmt.excluded.updated_at},
    )
    session.execute(stmt)
    logger.info("Upserted {} stock names", len(rows))


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(
        prog="python -m src.analysis.peak_corr",
        description="Compute zigzag peak-correlation and save to DB",
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--stock-set", metavar="SECTION")
    grp.add_argument("--code", nargs="+", metavar="CODE")
    p.add_argument("--stock-codes-file", default="configs/stock_codes.ini")
    p.add_argument("--start",          required=True)
    p.add_argument("--end",            default=None)
    p.add_argument("--granularity",    default="1d")
    p.add_argument("--zz-size",        type=int, default=5)
    p.add_argument("--zz-middle-size", type=int, default=2)
    args = p.parse_args(argv)

    if args.code:
        codes: list[str] = args.code
        stock_set = None
    else:
        from src.config import load_stock_codes
        codes    = load_stock_codes(args.stock_codes_file, args.stock_set)
        stock_set = args.stock_set
        logger.info("Loaded {} codes from [{}]", len(codes), args.stock_set)

    # Exclude major indicators from the "stocks" list
    ind_set    = set(MAJOR_INDICATORS)
    stock_only = [c for c in codes if c not in ind_set]

    start = _parse_dt(args.start)
    end   = _parse_dt(args.end) if args.end else datetime.datetime.now(datetime.timezone.utc)

    with get_session() as session:
        _upsert_names(session, codes)
        run_id = run_and_save(
            session, stock_only, start, end,
            gran=args.granularity,
            zz_size=args.zz_size,
            zz_middle_size=args.zz_middle_size,
            stock_set=stock_set,
        )
    logger.info("Done — peak_corr_run_id={}", run_id)


if __name__ == "__main__":
    main()
