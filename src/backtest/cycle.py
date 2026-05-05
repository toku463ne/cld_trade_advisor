"""Backtest preparation cycle: download → indicators → simulate.

Runs three phases in order for a given stock set and date range:

  Phase 1 — Download OHLCV data (skips dates already in DB)
  Phase 2 — Compute heavy indicators and cache to DB (skips covered dates)
             Currently: moving_corr (rolling return-correlation with major indices)
  Phase 3 — Run backtest / GA training for the chosen strategy

CLI
---
    # Full cycle with GA:
    uv run --env-file devenv python -m src.backtest.cycle \\
        --stock-set medium --start 2022-01-01 --end 2025-12-31 \\
        --strategy sma_breakout --trainer ga

    # Skip simulation (data + indicators only):
    uv run --env-file devenv python -m src.backtest.cycle \\
        --stock-set medium --start 2022-01-01 --end 2025-12-31 --no-sim

    # Single stock, grid search:
    uv run --env-file devenv python -m src.backtest.cycle \\
        --code 7203.T --start 2022-01-01 --end 2025-12-31 \\
        --strategy sma_breakout --trainer grid
"""

from __future__ import annotations

import argparse
import datetime
import sys

from loguru import logger

from src.analysis.peak_corr import MAJOR_INDICATORS


# ── Phase helpers ─────────────────────────────────────────────────────────────


def phase_download(
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str,
) -> None:
    """Phase 1: download OHLCV for all codes + major indicators."""
    from src.data.collect import OHLCVCollector
    from src.data.db import get_session

    all_codes = list(dict.fromkeys(MAJOR_INDICATORS + codes))
    logger.info("Phase 1 — Downloading OHLCV for {} codes …", len(all_codes))
    with get_session() as session:
        collector = OHLCVCollector(session)
        for code in all_codes:
            collector.collect(code, gran, start, end)
    logger.info("Phase 1 complete.")


def phase_indicators(
    stock_codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str,
    window: int,
    force: bool,
) -> None:
    """Phase 2: compute and cache heavy indicators to DB."""
    from src.analysis.moving_corr import compute_and_save
    from src.data.db import get_session

    logger.info(
        "Phase 2 — Computing moving_corr (window={}) for {} stocks × {} indicators …",
        window, len(stock_codes), len(MAJOR_INDICATORS),
    )
    with get_session() as session:
        saved = compute_and_save(
            session, stock_codes, MAJOR_INDICATORS,
            start, end, window_bars=window, gran=gran, force=force,
        )
    total = sum(saved.values())
    logger.info("Phase 2 complete — {} rows saved.", total)


def phase_simulate(
    codes: list[str],
    start: datetime.datetime,
    end: datetime.datetime,
    gran: str,
    strategy: str,
    trainer: str,
    units: int,
    ga_pop: int,
    ga_gen: int,
    ga_score_agg: str,
) -> None:
    """Phase 3: run backtest trainer (grid or GA)."""
    from src.backtest.train_models import main as trainer_main

    logger.info(
        "Phase 3 — Running {} trainer for strategy '{}' on {} codes …",
        trainer, strategy, len(codes),
    )
    argv = [
        "--strategy", strategy,
        "--trainer", trainer,
        "--start",   start.date().isoformat(),
        "--end",     end.date().isoformat(),
        "--granularity", gran,
        "--units",   str(units),
        "--ga-pop",  str(ga_pop),
        "--ga-gen",  str(ga_gen),
        "--ga-score-agg", ga_score_agg,
    ]
    for code in codes:
        argv += ["--code", code]

    trainer_main(argv)
    logger.info("Phase 3 complete.")


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(
        prog="python -m src.backtest.cycle",
        description="Full backtest cycle: download → indicators → simulate",
    )

    # Stock selection
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--stock-set", metavar="SECTION")
    grp.add_argument("--code", nargs="+", metavar="CODE")
    p.add_argument("--stock-codes-file", default="configs/stock_codes.ini")

    # Date range
    p.add_argument("--start",       required=True)
    p.add_argument("--end",         default=None)
    p.add_argument("--granularity", default="1d")

    # Phase 2 — indicators
    p.add_argument("--window", type=int, default=20, metavar="N",
                   help="Rolling window for moving_corr (default: 20)")
    p.add_argument("--force-indicators", action="store_true",
                   help="Recompute indicators even if data already exists")

    # Phase 3 — simulation
    p.add_argument("--no-sim", action="store_true",
                   help="Skip Phase 3 (data + indicators only)")
    p.add_argument("--strategy", default=None, metavar="NAME",
                   help="Strategy CLI name, e.g. sma_breakout (required unless --no-sim)")
    p.add_argument("--trainer", default="ga", choices=["grid", "ga"])
    p.add_argument("--units",        type=int, default=100)
    p.add_argument("--ga-pop",       type=int, default=60)
    p.add_argument("--ga-gen",       type=int, default=40)
    p.add_argument("--ga-score-agg", default="mean", choices=["mean", "min", "median"])

    args = p.parse_args(argv)

    if not args.no_sim and not args.strategy:
        p.error("--strategy is required unless --no-sim is set.")

    # Resolve codes
    if args.code:
        codes: list[str] = args.code
    else:
        from src.config import load_stock_codes
        codes = load_stock_codes(args.stock_codes_file, args.stock_set)
        logger.info("Loaded {} codes from [{}]", len(codes), args.stock_set)

    ind_set    = set(MAJOR_INDICATORS)
    stock_only = [c for c in codes if c not in ind_set]

    start = _parse_dt(args.start)
    end   = _parse_dt(args.end) if args.end else datetime.datetime.now(datetime.timezone.utc)

    logger.info(
        "Cycle: {} stocks | {} → {} | gran={}",
        len(stock_only), start.date(), end.date(), args.granularity,
    )

    phase_download(stock_only, start, end, args.granularity)
    phase_indicators(stock_only, start, end, args.granularity,
                     window=args.window, force=args.force_indicators)

    if not args.no_sim:
        phase_simulate(
            stock_only, start, end, args.granularity,
            strategy=args.strategy,
            trainer=args.trainer,
            units=args.units,
            ga_pop=args.ga_pop,
            ga_gen=args.ga_gen,
            ga_score_agg=args.ga_score_agg,
        )

    logger.info("Cycle complete.")


if __name__ == "__main__":
    main()
