"""Sliding-window return-correlation analysis.

Computes Pearson correlation of daily returns between every stock pair
over a rolling window, then summarises each pair as mean ± std across
all windows.

Why returns, not prices?
  Price correlation is almost always spurious — two stocks that both
  trend upward over a year will appear highly correlated even if their
  day-to-day movements are unrelated.  Return correlation captures the
  actual co-movement signal.

Why sliding windows?
  Market regimes change.  A pair that was highly correlated in 2022 may
  decouple in 2024.  Sliding windows give a distribution of correlation
  values; mean shows the average relationship and std shows stability.

CLI usage
---------
    # With a named stock set:
    uv run --env-file devenv python -m analysis.stock_corrs \\
        --stock-set medium --start 2022-01-01 --end 2025-12-31

    # With explicit codes:
    uv run --env-file devenv python -m analysis.stock_corrs \\
        --code 7203.T 6758.T 9984.T --start 2022-01-01 --end 2025-12-31

    # Custom window / step:
    uv run --env-file devenv python -m analysis.stock_corrs \\
        --stock-set medium --start 2022-01-01 --end 2025-12-31 \\
        --window 60 --step 20
"""

from __future__ import annotations

import argparse
import datetime
import sys
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf
from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.analysis.models import CorrRun, StockCorrPair
from src.data.db import get_session
from src.data.models import OHLCV_MODEL_MAP, Stock


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def _load_close_prices(
    session: Session,
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str,
) -> pd.DataFrame:
    """Return a DataFrame of close prices: index=date, columns=stock_code.

    Timestamps are normalised to their local calendar date so that stocks from
    different timezones (e.g. JP midnight vs US 14:00 JST) land on the same
    index row when they represent the same trading day.
    """
    model = OHLCV_MODEL_MAP[gran]
    price_map: dict[str, dict[datetime.date, float]] = {}

    for code in codes:
        stmt = (
            select(model.ts, model.close_price)
            .where(model.stock_code == code, model.ts >= start, model.ts < end)
            .order_by(model.ts)
        )
        rows = session.execute(stmt).all()
        if rows:
            # Use .date() to strip time-of-day differences across timezones
            price_map[code] = {r.ts.date(): r.close_price for r in rows}

    if not price_map:
        return pd.DataFrame()

    df = pd.DataFrame(price_map)
    df.index = pd.DatetimeIndex(df.index)
    df.sort_index(inplace=True)
    # Drop stocks that are missing more than half the bars
    df = df.dropna(axis=1, thresh=max(1, len(df) // 2))
    return df


def _sliding_pair_corrs(
    returns: pd.DataFrame,
    window_days: int,
    step_days: int,
) -> tuple[dict[tuple[str, str], list[float]], int]:
    """Return per-pair correlation lists and the number of windows processed."""
    codes      = list(returns.columns)
    pair_corrs: dict[tuple[str, str], list[float]] = {}
    n_bars     = len(returns)
    n_windows  = 0

    for start_idx in range(0, n_bars - window_days + 1, step_days):
        window = returns.iloc[start_idx : start_idx + window_days].dropna(how="all")
        # Require at least 80 % of bars to have data
        if len(window) < window_days * 0.8:
            continue

        corr_matrix = window.corr()
        n_windows  += 1

        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                a, b = codes[i], codes[j]
                val  = corr_matrix.loc[a, b]
                if pd.notna(val):
                    key = (a, b)
                    pair_corrs.setdefault(key, []).append(float(val))

    return pair_corrs, n_windows


def _aggregate(
    pair_corrs: dict[tuple[str, str], list[float]],
) -> list[dict[str, Any]]:
    """Compute mean / std per pair and sort by abs(round(mean,2)) desc, std asc."""
    rows: list[dict[str, Any]] = []
    for (a, b), vals in pair_corrs.items():
        if len(vals) < 2:
            continue
        arr  = np.array(vals, dtype=np.float64)
        mean = float(np.mean(arr))
        std  = float(np.std(arr, ddof=1))
        rows.append({"stock_a": a, "stock_b": b,
                     "mean_corr": mean, "std_corr": std, "n_windows": len(vals)})

    rows.sort(key=lambda r: (-abs(round(r["mean_corr"], 2)), r["std_corr"]))
    return rows


# ---------------------------------------------------------------------------
# Stock name helpers
# ---------------------------------------------------------------------------

def _upsert_stock_names(session: Session, codes: list[str]) -> None:
    """Fetch names from yfinance for any code not yet in the stocks table."""
    existing = {
        r.code for r in session.execute(
            select(Stock.code).where(Stock.code.in_(codes))
        ).all()
    }
    missing = [c for c in codes if c not in existing]
    if not missing:
        return

    logger.info("Fetching names for {} new stock codes …", len(missing))
    now = datetime.datetime.now(datetime.timezone.utc)
    rows: list[dict] = []
    for code in missing:
        try:
            info = yf.Ticker(code).info
            name = info.get("shortName") or info.get("longName") or code
        except Exception:
            name = code
        rows.append({"code": code, "name": name, "updated_at": now})
        logger.debug("  {} → {}", code, name)

    stmt = pg_insert(Stock).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={"name": stmt.excluded.name, "updated_at": stmt.excluded.updated_at},
    )
    session.execute(stmt)
    logger.info("Upserted {} stock names", len(rows))


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def run_and_save(
    session: Session,
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    window_days: int = 20,
    step_days: int   = 10,
    gran: str        = "1d",
) -> int:
    """Compute correlations and persist to DB.  Returns the new corr_run.id."""
    logger.info("Loading close prices for {} stocks ({} → {}) …", len(codes), start.date(), end.date())
    prices  = _load_close_prices(session, codes, start, end, gran)

    if prices.empty:
        raise RuntimeError("No price data found for the given codes / period.")

    active_codes = list(prices.columns)
    skipped      = set(codes) - set(active_codes)
    if skipped:
        logger.warning("Skipped {} stocks with insufficient data: {}", len(skipped), sorted(skipped))

    _upsert_stock_names(session, active_codes)

    logger.info("Computing returns for {} stocks …", len(active_codes))
    returns = prices.pct_change().dropna(how="all")

    logger.info(
        "Sliding window: window={} step={} bars={} → ~{} windows",
        window_days, step_days, len(returns),
        max(0, (len(returns) - window_days) // step_days + 1),
    )
    pair_corrs, n_windows = _sliding_pair_corrs(returns, window_days, step_days)
    logger.info("Processed {} windows, {} pairs", n_windows, len(pair_corrs))

    aggregated = _aggregate(pair_corrs)
    logger.info("Saving {} pairs to DB …", len(aggregated))

    run = CorrRun(
        start_dt    = start,
        end_dt      = end,
        granularity = gran,
        window_days = window_days,
        step_days   = step_days,
        n_stocks    = len(active_codes),
        n_windows   = n_windows,
        created_at  = datetime.datetime.now(datetime.timezone.utc),
    )
    session.add(run)
    session.flush()

    session.bulk_insert_mappings(
        StockCorrPair,  # type: ignore[arg-type]
        [{"corr_run_id": run.id, **row} for row in aggregated],
    )
    session.commit()
    return run.id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m analysis.stock_corrs",
        description="Compute sliding-window return correlations and save to DB",
    )
    stock_grp = p.add_mutually_exclusive_group(required=True)
    stock_grp.add_argument("--code", nargs="+", metavar="CODE")
    stock_grp.add_argument("--stock-set", metavar="SECTION")
    p.add_argument("--stock-codes-file", default="configs/stock_codes.ini")
    p.add_argument("--start", required=True)
    p.add_argument("--end",   default=None)
    p.add_argument("--granularity", default="1d")
    p.add_argument("--window", type=int, default=20, help="Window size in bars (default: 20)")
    p.add_argument("--step",   type=int, default=10, help="Step size in bars (default: 10)")
    return p


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    args = _build_parser().parse_args(argv)

    if args.code:
        codes: list[str] = args.code
    else:
        from src.config import load_stock_codes
        codes = load_stock_codes(args.stock_codes_file, args.stock_set)
        logger.info("Loaded {} codes from [{}]", len(codes), args.stock_set)

    start = _parse_dt(args.start)
    end   = _parse_dt(args.end) if args.end else datetime.datetime.now(datetime.timezone.utc)

    with get_session() as session:
        run_id = run_and_save(
            session, codes, start, end,
            window_days=args.window,
            step_days=args.step,
            gran=args.granularity,
        )
    logger.info("Done — corr_run_id={}", run_id)


if __name__ == "__main__":
    main()
