"""SQLAlchemy ORM models for J-Quants API data (parallel `jq_`-prefixed source).

These tables coexist with the existing yfinance-fed ``stocks`` / ``ohlcv_1d_*``
schema and never touch it.  J-Quants identifies stocks by a 5-digit ``LocalCode``
(e.g. ``"13010"`` for 1301); :func:`src.data.jquants_collector.to_yf_code` maps it
to the existing ``"1301.T"`` form when a join is needed.

Source endpoints (https://api.jquants.com/v1):
  jq_listed            <- /listed/info
  jq_daily_quotes      <- /prices/daily_quotes
  jq_statements        <- /fins/statements
  jq_topix             <- /indices/topix
  jq_trading_calendar  <- /markets/trading_calendar
  jq_fetch_cursor      <- internal resume bookmark (one row per endpoint)

PEAD note: the earnings *event* is ``jq_statements.announcement_date`` (= the
J-Quants ``DisclosedDate``).  J-Quants has no literal ``first_published_at`` field;
the *first* disclosure of a result is the row with the lowest ``disclosure_number``
for a given ``(local_code, type_of_current_period, current_period_end_date)`` —
later revisions reuse the period with a higher number.  ``disclosed_time`` is kept
raw so the PEAD probe can shift an after-close (>=15:00) disclosure to the next
trading session before applying the two-bar fill.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Index, Numeric, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models import Base

_PRICE = Numeric(14, 4)
_YEN = Numeric(22, 2)   # profit/sales figures in yen, can be large and negative


class JqListed(Base):
    """Listed-company master snapshot from /listed/info."""

    __tablename__ = "jq_listed"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)  # LocalCode
    date: Mapped[datetime.date | None] = mapped_column(Date)         # snapshot date
    company_name: Mapped[str | None] = mapped_column(String(500))
    company_name_en: Mapped[str | None] = mapped_column(String(500))
    sector17_code: Mapped[str | None] = mapped_column(String(10))
    sector17_name: Mapped[str | None] = mapped_column(String(200))
    sector33_code: Mapped[str | None] = mapped_column(String(10))
    sector33_name: Mapped[str | None] = mapped_column(String(200))
    scale_category: Mapped[str | None] = mapped_column(String(100))
    market_code: Mapped[str | None] = mapped_column(String(10))
    market_code_name: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )


class JqDailyQuote(Base):
    """Adjusted + raw daily OHLCV from /prices/daily_quotes."""

    __tablename__ = "jq_daily_quotes"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(_PRICE)
    high: Mapped[Decimal | None] = mapped_column(_PRICE)
    low: Mapped[Decimal | None] = mapped_column(_PRICE)
    close: Mapped[Decimal | None] = mapped_column(_PRICE)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    turnover_value: Mapped[Decimal | None] = mapped_column(_YEN)
    adjustment_factor: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    adj_open: Mapped[Decimal | None] = mapped_column(_PRICE)
    adj_high: Mapped[Decimal | None] = mapped_column(_PRICE)
    adj_low: Mapped[Decimal | None] = mapped_column(_PRICE)
    adj_close: Mapped[Decimal | None] = mapped_column(_PRICE)
    adj_volume: Mapped[int | None] = mapped_column(BigInteger)


class JqStatement(Base):
    """Financial-statement disclosure from /fins/statements (one row per disclosure)."""

    __tablename__ = "jq_statements"

    disclosure_number: Mapped[str] = mapped_column(String(20), primary_key=True)
    local_code: Mapped[str] = mapped_column(String(10), nullable=False)
    disclosed_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    disclosed_time: Mapped[datetime.time | None] = mapped_column(Time)
    # announcement_date == DisclosedDate; the PEAD event anchor (see module docstring).
    announcement_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    type_of_document: Mapped[str | None] = mapped_column(String(100))
    type_of_current_period: Mapped[str | None] = mapped_column(String(10))  # 1Q/2Q/3Q/FY
    current_period_start_date: Mapped[datetime.date | None] = mapped_column(Date)
    current_period_end_date: Mapped[datetime.date | None] = mapped_column(Date)
    current_fiscal_year_start_date: Mapped[datetime.date | None] = mapped_column(Date)
    current_fiscal_year_end_date: Mapped[datetime.date | None] = mapped_column(Date)
    net_sales: Mapped[Decimal | None] = mapped_column(_YEN)
    operating_profit: Mapped[Decimal | None] = mapped_column(_YEN)
    ordinary_profit: Mapped[Decimal | None] = mapped_column(_YEN)
    profit: Mapped[Decimal | None] = mapped_column(_YEN)
    earnings_per_share: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    forecast_operating_profit: Mapped[Decimal | None] = mapped_column(_YEN)
    forecast_ordinary_profit: Mapped[Decimal | None] = mapped_column(_YEN)
    forecast_profit: Mapped[Decimal | None] = mapped_column(_YEN)
    forecast_earnings_per_share: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    # Balance-sheet / per-share fields for PBR (price/BPS) and ROE (profit/equity).
    total_assets: Mapped[Decimal | None] = mapped_column(_YEN)
    equity: Mapped[Decimal | None] = mapped_column(_YEN)
    equity_to_asset_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    book_value_per_share: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    shares_outstanding_fy: Mapped[int | None] = mapped_column(BigInteger)  # incl. treasury
    treasury_shares_fy: Mapped[int | None] = mapped_column(BigInteger)
    average_shares: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    retrieved_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    __table_args__ = (
        Index("ix_jq_statements_code_disc", "local_code", "disclosed_date"),
        Index("ix_jq_statements_period",
              "local_code", "type_of_current_period", "current_period_end_date"),
    )


class JqTopix(Base):
    """TOPIX index daily OHLC from /indices/topix."""

    __tablename__ = "jq_topix"

    date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(_PRICE)
    high: Mapped[Decimal | None] = mapped_column(_PRICE)
    low: Mapped[Decimal | None] = mapped_column(_PRICE)
    close: Mapped[Decimal | None] = mapped_column(_PRICE)


class JqTradingCalendar(Base):
    """Exchange trading calendar from /markets/trading_calendar.

    holiday_division: "0"=non-business, "1"=business day, "2"=day before holiday
    (half-ish), "3"=non-business but ToSTNeT/OSE open — store raw J-Quants code.
    """

    __tablename__ = "jq_trading_calendar"

    date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    holiday_division: Mapped[str | None] = mapped_column(String(2))


class JqFetchCursor(Base):
    """Resume bookmark — one row per endpoint records the last date fetched."""

    __tablename__ = "jq_fetch_cursor"

    endpoint: Mapped[str] = mapped_column(String(40), primary_key=True)
    last_date: Mapped[datetime.date | None] = mapped_column(Date)
    last_pagination_key: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
