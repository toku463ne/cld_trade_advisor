"""OHLCV data collector — orchestrator and CLI entry point.

Usage:
    uv run --env-file devenv python -m src.data.collect ohlcv \\
        --code 7203.T --granularity 1d --start 2020-01-01 --end 2024-12-31

    uv run --env-file devenv python -m src.data.collect stocks --update
    uv run --env-file devenv python -m src.data.collect stocks --list
"""

from __future__ import annotations

import argparse
import datetime
import sys
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.data.db import get_session
from src.data.downloader import YFinanceDownloader
from src.data.models import GRANULARITIES, OHLCV_MODEL_MAP, _OhlcvBase
from src.data.partitions import ensure_partitions
from src.data.stocks import StockManager


class OHLCVCollector:
    """Collects OHLCV data, downloading only what is missing from the DB."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._downloader = YFinanceDownloader()

    def collect(
        self,
        code: str,
        gran: str,
        start: datetime.datetime,
        end: datetime.datetime,
    ) -> int:
        """Download and store OHLCV data, skipping date ranges already in DB.

        Returns the number of rows inserted.
        """
        if gran not in OHLCV_MODEL_MAP:
            raise ValueError(f"Unknown granularity {gran!r}. Choose from {GRANULARITIES}")

        model = OHLCV_MODEL_MAP[gran]
        gaps = self._missing_ranges(model, code, start, end)
        if not gaps:
            logger.info("No missing data for {} {} {} – {}", code, gran, start.date(), end.date())
            return 0

        total_inserted = 0
        for gap_start, gap_end in gaps:
            logger.info(
                "Fetching {} {} {} – {}", code, gran, gap_start.date(), gap_end.date()
            )
            rows = self._downloader.fetch(code, gran, gap_start, gap_end)
            if not rows:
                continue

            ensure_partitions(self._session, gran, gap_start, gap_end)

            stmt = pg_insert(model).values(rows)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["stock_code", "ts"]
            )
            result = self._session.execute(stmt)
            self._session.commit()
            total_inserted += result.rowcount or 0

        logger.info("Inserted {} rows for {} {}", total_inserted, code, gran)
        return total_inserted

    # ------------------------------------------------------------------
    # Gap detection
    # ------------------------------------------------------------------

    def _missing_ranges(
        self,
        model: type[_OhlcvBase],
        code: str,
        start: datetime.datetime,
        end: datetime.datetime,
    ) -> list[tuple[datetime.datetime, datetime.datetime]]:
        stmt = select(func.min(model.ts), func.max(model.ts)).where(
            model.stock_code == code,
            model.ts >= start,
            model.ts < end,
        )
        db_min, db_max = self._session.execute(stmt).one()
        return compute_gaps(start, end, db_min, db_max)


def compute_gaps(
    req_start: datetime.datetime,
    req_end: datetime.datetime,
    db_min: datetime.datetime | None,
    db_max: datetime.datetime | None,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Return date ranges within [req_start, req_end) not covered by DB data.

    Pure function — no I/O, easy to unit test.
    """
    if db_min is None:
        return [(req_start, req_end)]

    gaps: list[tuple[datetime.datetime, datetime.datetime]] = []
    if db_min > req_start:
        gaps.append((req_start, db_min))
    if db_max is not None and db_max < req_end:
        gaps.append((db_max, req_end))
    return gaps


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_dt(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.data.collect",
        description="Collect Japanese stock OHLCV data into PostgreSQL",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- ohlcv sub-command ---
    ohlcv_p = sub.add_parser("ohlcv", help="Download OHLCV price data")
    ohlcv_p.add_argument(
        "--config", default=None, metavar="YAML",
        help="YAML config file; CLI flags override values in the file",
    )
    # Stock selection: explicit codes OR a named set from stock_codes.ini
    stock_grp = ohlcv_p.add_mutually_exclusive_group(required=False)
    stock_grp.add_argument(
        "--code", nargs="+", metavar="CODE", default=None,
        help="Explicit stock codes, e.g. --code 7203.T 6758.T",
    )
    stock_grp.add_argument(
        "--stock-set", default=None, metavar="SECTION",
        help="Named stock set from --stock-codes-file, e.g. --stock-set universe",
    )
    ohlcv_p.add_argument(
        "--stock-codes-file", default="configs/stock_codes.ini", metavar="INI",
        help="Path to stock_codes.ini (default: configs/stock_codes.ini)",
    )
    ohlcv_p.add_argument(
        "--granularity",
        default="1d",
        choices=list(GRANULARITIES),
        help="Time granularity (default: 1d)",
    )
    ohlcv_p.add_argument(
        "--start", default=None,
        help="Start date/datetime (ISO format, e.g. 2020-01-01)",
    )
    ohlcv_p.add_argument(
        "--end", default=None,
        help="End date/datetime (ISO format). Defaults to today.",
    )

    # --- stocks sub-command ---
    stocks_p = sub.add_parser("stocks", help="Manage the stock master list")
    group = stocks_p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--update",
        action="store_true",
        help="Download and upsert the JPX stock list",
    )
    group.add_argument(
        "--list",
        action="store_true",
        dest="list_stocks",
        help="Print all active stocks in the DB",
    )

    return parser


def _setup_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add("logs/collect.log", level="DEBUG", rotation="1 week", retention="4 weeks")


def main(argv: list[str] | None = None) -> None:
    _setup_logging()
    parser = _build_parser()

    # ── Apply YAML defaults before full parse ────────────────────────────
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("command", nargs="?")
    _pre.add_argument("--config", default=None)
    _pre_args, _ = _pre.parse_known_args(argv)
    if _pre_args.config:
        from src.config import collect_defaults, load_yaml
        cfg = load_yaml(_pre_args.config)
        # set_defaults on the ohlcv sub-parser via the main parser's _subparsers
        for action in parser._subparsers._group_actions:  # type: ignore[attr-defined]
            for name, sp in action.choices.items():
                if name == "ohlcv":
                    sp.set_defaults(**collect_defaults(cfg))

    args = parser.parse_args(argv)

    with get_session() as session:
        if args.command == "ohlcv":
            # ── Resolve stock codes ───────────────────────────────────────
            if args.code:
                codes: list[str] = args.code
            elif args.stock_set:
                from src.config import load_stock_codes
                codes = load_stock_codes(args.stock_codes_file, args.stock_set)
                logger.info(
                    "Loaded {} codes from [{}] in {}",
                    len(codes), args.stock_set, args.stock_codes_file,
                )
            else:
                parser.error(
                    "Provide --code, --stock-set, or set stock_set in a --config file."
                )
                return

            if not args.start:
                parser.error("--start is required (or set data.start in the config file).")

            start = _parse_dt(args.start)
            end = (
                _parse_dt(args.end)
                if args.end
                else datetime.datetime.now(datetime.timezone.utc)
            )
            collector = OHLCVCollector(session)
            for code in codes:
                collector.collect(code, args.granularity, start, end)

        elif args.command == "stocks":
            mgr = StockManager(session)
            if args.update:
                n = mgr.download_and_update()
                logger.info("Stock list updated: {} records", n)
            else:
                for stock in mgr.list_stocks():
                    print(f"{stock.code}\t{stock.name}")


if __name__ == "__main__":
    main()
