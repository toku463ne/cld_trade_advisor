"""On-demand PostgreSQL partition creation for OHLCV tables.

Partitioned parent tables are created by Alembic migrations.
This module creates child partitions at runtime before data inserts.
DDL (CREATE TABLE ... PARTITION OF) cannot be expressed through the SQLAlchemy
ORM and is intentionally handled with text() here.
"""

from __future__ import annotations

import datetime
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session


def ensure_partitions(
    session: Session,
    gran: str,
    start: datetime.datetime,
    end: datetime.datetime,
) -> None:
    """Create any missing partitions covering [start, end) for the given granularity.

    Idempotent — uses CREATE TABLE IF NOT EXISTS.
    Commits the DDL immediately so subsequent inserts can use the partitions.
    """
    table_name = f"ohlcv_{gran}"
    if gran in ("1d", "1wk"):
        ddl_statements = [
            _yearly_partition_ddl(table_name, year)
            for year in _iter_year_range(start, end)
        ]
    else:
        ddl_statements = [
            _monthly_partition_ddl(table_name, year, month)
            for year, month in _iter_month_range(start, end)
        ]

    for ddl in ddl_statements:
        session.execute(text(ddl))

    session.commit()


def _yearly_partition_ddl(table_name: str, year: int) -> str:
    pname = f"{table_name}_y{year}"
    return (
        f"CREATE TABLE IF NOT EXISTS {pname} "
        f"PARTITION OF {table_name} "
        f"FOR VALUES FROM ('{year}-01-01 00:00:00+00') TO ('{year + 1}-01-01 00:00:00+00')"
    )


def _monthly_partition_ddl(table_name: str, year: int, month: int) -> str:
    pname = f"{table_name}_y{year}m{month:02d}"
    if month == 12:
        end_year, end_month = year + 1, 1
    else:
        end_year, end_month = year, month + 1
    return (
        f"CREATE TABLE IF NOT EXISTS {pname} "
        f"PARTITION OF {table_name} "
        f"FOR VALUES FROM ('{year}-{month:02d}-01 00:00:00+00') "
        f"TO ('{end_year}-{end_month:02d}-01 00:00:00+00')"
    )


def _iter_year_range(start: datetime.datetime, end: datetime.datetime) -> Iterator[int]:
    for year in range(start.year, end.year + 1):
        yield year


def _iter_month_range(
    start: datetime.datetime, end: datetime.datetime
) -> Iterator[tuple[int, int]]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
