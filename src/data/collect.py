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
        "--code",
        required=True,
        nargs="+",
        metavar="CODE",
        help="Stock code(s), e.g. 7203.T 6758.T",
    )
    ohlcv_p.add_argument(
        "--granularity",
        required=True,
        choices=list(GRANULARITIES),
        help="Time granularity",
    )
    ohlcv_p.add_argument(
        "--start",
        required=True,
        help="Start date/datetime (ISO format, e.g. 2020-01-01)",
    )
    ohlcv_p.add_argument(
        "--end",
        default=None,
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
    args = parser.parse_args(argv)

    with get_session() as session:
        if args.command == "ohlcv":
            start = _parse_dt(args.start)
            end = (
                _parse_dt(args.end)
                if args.end
                else datetime.datetime.now(datetime.timezone.utc)
            )
            collector = OHLCVCollector(session)
            for code in args.code:
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
