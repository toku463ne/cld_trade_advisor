"""DB models for persisting simulation results.

These tables store post-run history only. No DB writes happen during
the simulation itself.
"""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.models import Base


class SimRun(Base):
    """Metadata for one simulation run."""

    __tablename__ = "sim_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False)
    gran: Mapped[str] = mapped_column(String(10), nullable=False)
    start_dt: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_dt: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False)
    final_equity: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    orders: Mapped[list["SimOrder"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    trades: Mapped[list["SimTrade"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    position: Mapped["SimPosition | None"] = relationship(back_populates="run", uselist=False, cascade="all, delete-orphan")


class SimOrder(Base):
    """Order history for a simulation run."""

    __tablename__ = "sim_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sim_runs.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False)   # simulator-internal id
    side: Mapped[int] = mapped_column(SmallInteger, nullable=False)   # 1=buy, -1=sell
    order_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1=market,2=limit,3=stop
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # OrderStatus int value
    filled_price: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    run: Mapped["SimRun"] = relationship(back_populates="orders")


class SimTrade(Base):
    """Record of each filled order (trade execution)."""

    __tablename__ = "sim_trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sim_runs.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    dt: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    run: Mapped["SimRun"] = relationship(back_populates="trades")


class SimPosition(Base):
    """Final open position snapshot at end of run."""

    __tablename__ = "sim_positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sim_runs.id"), nullable=False, unique=True
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    entry_dt: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    run: Mapped["SimRun"] = relationship(back_populates="position")
