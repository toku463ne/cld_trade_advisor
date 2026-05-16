"""Portfolio ORM models — Position tracking for manually executed trades."""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.data.models import Base


class Account(Base):
    """Virtual account scoping positions and reviewed candidates.

    Lets the operator run separate simulation tests in parallel
    (e.g. "naive baseline" vs "discretionary skip on AND-HIGH") without
    polluting each other's portfolio statistics.  A "default" account
    is created during migration so existing positions / reviews stay
    addressable.
    """

    __tablename__ = "accounts"

    id:           Mapped[int]  = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name:         Mapped[str]  = mapped_column(String(60), nullable=False, unique=True)
    description:  Mapped[str | None]   = mapped_column(Text, nullable=True)
    initial_cash: Mapped[float | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True,
    )
    archived:     Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at:   Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<Account id={self.id} name={self.name!r} archived={self.archived}>"


class Position(Base):
    """A manually entered trade position.

    Registered by the user after executing an order.  TP/SL levels are
    pre-computed from the ZsTpSl rule at registration time; the status field
    reflects the last user-confirmed state.
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

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

    status:      Mapped[str]                  = mapped_column(String(10), nullable=False, default="open")
    exit_date:   Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    exit_price:  Mapped[float | None]         = mapped_column(Numeric(precision=12, scale=2), nullable=True)
    exit_reason: Mapped[str | None]           = mapped_column(String(16), nullable=True)
    notes:       Mapped[str | None]           = mapped_column(Text, nullable=True)

    # Context at registration time — populated automatically from the proposal
    # row and the regime indicators; powers post-hoc "what was the regime when
    # I took this trade?" analysis on the 2025 simulation cohort.
    sign_score:  Mapped[float | None] = mapped_column(Float, nullable=True)
    revn_frac:   Mapped[float | None] = mapped_column(Float, nullable=True)
    sma_frac:    Mapped[float | None] = mapped_column(Float, nullable=True)
    corr_frac:   Mapped[float | None] = mapped_column(Float, nullable=True)

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


class ReviewedCandidate(Base):
    """One operator decision on a proposal: taken or skipped.

    Written for every explicit Skip / Register click in the Daily-tab UI.
    A "taken" review carries a foreign-key to the resulting Position;
    a "skipped" review captures the regime context + reason but no
    position is created.  Lets post-hoc analysis answer: 'did my
    discretion beat systematic acceptance?'
    """

    __tablename__ = "reviewed_candidates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    fired_at:   Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    stock_code: Mapped[str]           = mapped_column(String(20), nullable=False)
    sign_type:  Mapped[str]           = mapped_column(String(30), nullable=False)

    sign_score: Mapped[float | None]  = mapped_column(Float, nullable=True)
    corr_mode:  Mapped[str | None]    = mapped_column(String(10), nullable=True)
    corr_n225:  Mapped[float | None]  = mapped_column(Float, nullable=True)
    kumo_state: Mapped[int | None]    = mapped_column(Integer, nullable=True)

    action:      Mapped[str]          = mapped_column(String(16), nullable=False)  # "taken" | "skipped"
    position_id: Mapped[int | None]   = mapped_column(
        BigInteger,
        ForeignKey("positions.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason:      Mapped[str | None]   = mapped_column(Text, nullable=True)

    revn_frac:   Mapped[float | None] = mapped_column(Float, nullable=True)
    sma_frac:    Mapped[float | None] = mapped_column(Float, nullable=True)
    corr_frac:   Mapped[float | None] = mapped_column(Float, nullable=True)

    reviewed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<ReviewedCandidate id={self.id} {self.stock_code} {self.sign_type} "
            f"action={self.action} fired={self.fired_at}>"
        )


class Memo(Base):
    """Free-form memo tied to a calendar date.

    Operator-written notes captured during daily simulation / live use.
    The Ideas sub-tab lists all memos with a link back to the Daily tab
    for the memo's date — restoring the full chart + regime context for
    review.
    """

    __tablename__ = "memos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    memo_date:  Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    content:    Mapped[str]           = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        snippet = (self.content[:40] + "…") if len(self.content) > 40 else self.content
        return f"<Memo id={self.id} date={self.memo_date} {snippet!r}>"
