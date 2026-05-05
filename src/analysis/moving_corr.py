"""Moving-correlation DB persistence and CLI.

Computes per-bar rolling return-correlation between stocks and major
indicators, storing results in the ``moving_corr`` table for fast retrieval
during backtests.

CLI
---
    # Compute for a named stock set (skips already-covered bars):
    uv run --env-file devenv python -m src.analysis.moving_corr \\
        --stock-set medium --start 2022-01-01 --end 2025-12-31 --window 20

    # Force recompute even if data already exists:
    uv run --env-file devenv python -m src.analysis.moving_corr \\
        --stock-set medium --start 2022-01-01 --end 2025-12-31 --window 20 --force

    # Explicit codes, hourly granularity:
    uv run --env-file devenv python -m src.analysis.moving_corr \\
        --code 7203.T 6758.T --start 2024-01-01 --end 2025-12-31 \\
        --granularity 1h --window 40
"""

from __future__ import annotations

import argparse
import datetime
import sys

import pandas as pd
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.analysis.models import MovingCorr, StockClusterMember, StockClusterRun
from src.analysis.peak_corr import MAJOR_INDICATORS, _load_field
from src.data.db import get_session
from src.indicators.moving_corr import compute_moving_corr


# ── DB helpers ────────────────────────────────────────────────────────────────


def save_moving_corr(
    session: Session,
    stock_code: str,
    indicator: str,
    gran: str,
    window_bars: int,
    series: pd.Series,
) -> int:
    """Upsert *series* (DatetimeIndex → corr_value) into moving_corr.

    Returns the number of rows written.
    """
    rows = [
        {
            "stock_code":  stock_code,
            "indicator":   indicator,
            "granularity": gran,
            "window_bars": window_bars,
            "ts":          ts.to_pydatetime().replace(tzinfo=datetime.timezone.utc),
            "corr_value":  float(v),
        }
        for ts, v in series.items()
        if pd.notna(v)
    ]
    if not rows:
        return 0
    stmt = pg_insert(MovingCorr).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_moving_corr",
        set_={"corr_value": stmt.excluded.corr_value},
    )
    result = session.execute(stmt)
    session.commit()
    return result.rowcount or len(rows)


def load_moving_corr(
    session: Session,
    stock_code: str,
    indicator: str,
    gran: str,
    window_bars: int,
    start: datetime.datetime,
    end: datetime.datetime,
) -> pd.Series:
    """Return stored correlation values as a DatetimeIndex Series."""
    rows = session.execute(
        select(MovingCorr.ts, MovingCorr.corr_value)
        .where(
            MovingCorr.stock_code  == stock_code,
            MovingCorr.indicator   == indicator,
            MovingCorr.granularity == gran,
            MovingCorr.window_bars == window_bars,
            MovingCorr.ts          >= start,
            MovingCorr.ts          <  end,
        )
        .order_by(MovingCorr.ts)
    ).all()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        [r.corr_value for r in rows],
        index=pd.DatetimeIndex([r.ts for r in rows]),
    )


def _covered_end(
    session: Session,
    stock_code: str,
    indicator: str,
    gran: str,
    window_bars: int,
) -> datetime.date | None:
    """Return the latest date already stored for (stock, indicator, gran, window)."""
    ts = session.execute(
        select(func.max(MovingCorr.ts)).where(
            MovingCorr.stock_code  == stock_code,
            MovingCorr.indicator   == indicator,
            MovingCorr.granularity == gran,
            MovingCorr.window_bars == window_bars,
        )
    ).scalar()
    return ts.date() if ts is not None else None


# ── Core computation + persistence ────────────────────────────────────────────


def compute_and_save(
    session: Session,
    stock_codes: list[str],
    indicators: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    window_bars: int = 20,
    gran: str = "1d",
    force: bool = False,
) -> dict[str, int]:
    """Compute moving correlations and persist them, skipping covered ranges.

    Loads the full price history once, then for each (stock, indicator) pair
    determines the uncovered date range, computes on the full series (so the
    rolling window is always warm), and saves only the new portion.

    Returns
    -------
    dict mapping stock_code → total rows saved.
    """
    all_codes = list(dict.fromkeys(indicators + stock_codes))

    logger.info("Loading close prices for {} codes …", len(all_codes))
    df_close = _load_field(session, all_codes, "close_price", start, end, gran)
    if df_close.empty:
        raise RuntimeError("No close price data found for the given period.")

    avail_inds   = [c for c in indicators  if c in df_close.columns]
    avail_stocks = [c for c in stock_codes if c in df_close.columns
                    and c not in set(avail_inds)]

    if not avail_inds:
        logger.warning("None of the requested indicators found in DB; aborting.")
        return {}

    indicator_map = {c: df_close[c].dropna() for c in avail_inds}
    req_end_date  = end.date()

    saved: dict[str, int] = {}
    for stock in avail_stocks:
        stock_series = df_close[stock].dropna()
        total = 0

        for ind_code, ind_series in indicator_map.items():
            if force:
                cutoff = pd.Timestamp(start.date())
            else:
                covered = _covered_end(session, stock, ind_code, gran, window_bars)
                if covered is not None and covered >= req_end_date:
                    logger.debug("  {}/{} already fully covered", stock, ind_code)
                    continue
                cutoff = pd.Timestamp(
                    covered + datetime.timedelta(days=1)
                    if covered else start.date()
                )

            # Compute on full series (warm-up included), slice to new portion
            corr_map   = compute_moving_corr(stock_series, {ind_code: ind_series},
                                             window=window_bars)
            corr_slice = corr_map[ind_code]
            corr_slice = corr_slice[corr_slice.index >= cutoff]

            n = save_moving_corr(session, stock, ind_code, gran, window_bars, corr_slice)
            total += n
            if n:
                logger.debug("  {}/{}: {} rows from {}", stock, ind_code, n, cutoff.date())

        if total:
            logger.info("  {} → {} rows saved", stock, total)
        saved[stock] = total

    return saved


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(
        prog="python -m src.analysis.moving_corr",
        description="Compute per-bar moving correlation and save to DB",
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--stock-set",   metavar="SECTION",
                     help="Named section in --stock-codes-file")
    grp.add_argument("--cluster-set", metavar="LABEL",
                     help="Load representative stocks from DB by fiscal-year label (e.g. classified2023)")
    grp.add_argument("--code", nargs="+", metavar="CODE")
    p.add_argument("--stock-codes-file", default="configs/stock_codes.ini")
    p.add_argument("--start",       required=True)
    p.add_argument("--end",         default=None)
    p.add_argument("--granularity", default="1d",
                   help="Bar granularity (default: 1d)")
    p.add_argument("--window",      type=int, default=20, metavar="N",
                   help="Rolling window in bars (default: 20)")
    p.add_argument("--force",       action="store_true",
                   help="Recompute and overwrite even if data already exists")
    args = p.parse_args(argv)

    if args.code:
        codes: list[str] = args.code
        stock_set = None
    elif args.cluster_set:
        with get_session() as _s:
            run = _s.execute(
                select(StockClusterRun)
                .where(StockClusterRun.fiscal_year == args.cluster_set)
            ).scalar_one_or_none()
            if run is None:
                p.error(f"No StockClusterRun found for fiscal_year={args.cluster_set!r}")
            codes = [
                m.stock_code for m in _s.execute(
                    select(StockClusterMember)
                    .where(StockClusterMember.run_id == run.id,
                           StockClusterMember.is_representative.is_(True))
                ).scalars().all()
            ]
        stock_set = args.cluster_set
        logger.info("Loaded {} representative stocks from cluster set [{}]", len(codes), stock_set)
    else:
        from src.config import load_stock_codes
        codes     = load_stock_codes(args.stock_codes_file, args.stock_set)
        stock_set = args.stock_set
        logger.info("Loaded {} codes from [{}]", len(codes), stock_set)

    ind_set    = set(MAJOR_INDICATORS)
    stock_only = [c for c in codes if c not in ind_set]

    start = _parse_dt(args.start)
    end   = _parse_dt(args.end) if args.end else datetime.datetime.now(datetime.timezone.utc)

    with get_session() as session:
        saved = compute_and_save(
            session, stock_only, MAJOR_INDICATORS,
            start, end,
            window_bars=args.window,
            gran=args.granularity,
            force=args.force,
        )

    total_rows = sum(saved.values())
    logger.info("Done — {} rows saved across {} stocks", total_rows, len(saved))


if __name__ == "__main__":
    main()
