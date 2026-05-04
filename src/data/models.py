"""SQLAlchemy ORM models for stock data storage."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, ClassVar, Literal

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

Granularity = Literal["1m", "5m", "15m", "30m", "1h", "1d", "1wk"]

GRANULARITIES: tuple[Granularity, ...] = ("1m", "5m", "15m", "30m", "1h", "1d", "1wk")
INTRADAY_GRANULARITIES: frozenset[str] = frozenset({"1m", "5m", "15m", "30m", "1h"})
DAILY_GRANULARITIES: frozenset[str] = frozenset({"1d", "1wk"})


class Base(DeclarativeBase):
    pass


class Stock(Base):
    """Master list of Japanese exchange-listed stocks."""

    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    market: Mapped[str | None] = mapped_column(String(100))
    sector33: Mapped[str | None] = mapped_column(String(200))
    sector17: Mapped[str | None] = mapped_column(String(200))
    scale: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<Stock code={self.code} name={self.name}>"


class _OhlcvBase(Base):
    """Abstract base for partitioned OHLCV tables.

    Each concrete subclass sets _gran to define its table name and
    partitioning strategy. Tables are PARTITION BY RANGE (ts).
    """

    __abstract__ = True
    _gran: ClassVar[str]

    @declared_attr
    def stock_code(cls) -> Mapped[str]:
        return mapped_column(String(20), primary_key=True)

    @declared_attr
    def ts(cls) -> Mapped[datetime.datetime]:
        return mapped_column(DateTime(timezone=True), primary_key=True)

    @declared_attr
    def open_price(cls) -> Mapped[Decimal]:
        return mapped_column(Numeric(14, 4), nullable=False)

    @declared_attr
    def high_price(cls) -> Mapped[Decimal]:
        return mapped_column(Numeric(14, 4), nullable=False)

    @declared_attr
    def low_price(cls) -> Mapped[Decimal]:
        return mapped_column(Numeric(14, 4), nullable=False)

    @declared_attr
    def close_price(cls) -> Mapped[Decimal]:
        return mapped_column(Numeric(14, 4), nullable=False)

    @declared_attr
    def volume(cls) -> Mapped[int]:
        return mapped_column(BigInteger, nullable=False)

    @declared_attr.directive
    @classmethod
    def __tablename__(cls) -> str:
        return f"ohlcv_{cls._gran}"

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> Any:
        tname = f"ohlcv_{cls._gran}"
        return (
            Index(f"ix_{tname}_ts", "ts"),
            {"postgresql_partition_by": "RANGE (ts)"},
        )


class Ohlcv1m(_OhlcvBase):
    _gran = "1m"


class Ohlcv5m(_OhlcvBase):
    _gran = "5m"


class Ohlcv15m(_OhlcvBase):
    _gran = "15m"


class Ohlcv30m(_OhlcvBase):
    _gran = "30m"


class Ohlcv1h(_OhlcvBase):
    _gran = "1h"


class Ohlcv1d(_OhlcvBase):
    _gran = "1d"


class Ohlcv1wk(_OhlcvBase):
    _gran = "1wk"


OHLCV_MODEL_MAP: dict[str, type[_OhlcvBase]] = {
    "1m": Ohlcv1m,
    "5m": Ohlcv5m,
    "15m": Ohlcv15m,
    "30m": Ohlcv30m,
    "1h": Ohlcv1h,
    "1d": Ohlcv1d,
    "1wk": Ohlcv1wk,
}
