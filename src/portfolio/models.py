"""Portfolio ORM models — Position tracking for manually executed trades."""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models import Base


class Position(Base):
    """A manually entered trade position.

    Registered by the user after executing an order.  TP/SL levels are
    pre-computed from the ZsTpSl rule at registration time; the status field
    reflects the last user-confirmed state.
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    stock_code: Mapped[str] = mapped_column(String(20), nullable=False)
    sign_type:  Mapped[str] = mapped_column(String(30), nullable=False)
    corr_mode:  Mapped[str] = mapped_column(String(10), nullable=False)
    kumo_state: Mapped[int] = mapped_column(Integer, nullable=False)

    direction:   Mapped[str]           = mapped_column(String(5), nullable=False, default="long")

    fired_at:    Mapped[datetime.date] = mapped_column(Date, nullable=False)
    entry_date:  Mapped[datetime.date] = mapped_column(Date, nullable=False)
    entry_price: Mapped[float]         = mapped_column(Numeric(precision=12, scale=2), nullable=False)
    units:       Mapped[int]           = mapped_column(Integer, nullable=False, default=100)

    tp_price: Mapped[float | None] = mapped_column(Numeric(precision=12, scale=2), nullable=True)
    sl_price: Mapped[float | None] = mapped_column(Numeric(precision=12, scale=2), nullable=True)

    status:     Mapped[str]                 = mapped_column(String(10), nullable=False, default="open")
    exit_date:  Mapped[datetime.date | None]  = mapped_column(Date, nullable=True)
    exit_price: Mapped[float | None]          = mapped_column(Numeric(precision=12, scale=2), nullable=True)
    notes:      Mapped[str | None]            = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<Position id={self.id} stock={self.stock_code} "
            f"entry={self.entry_date} status={self.status}>"
        )
