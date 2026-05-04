"""ORM models and persistence helpers for grid-search training results."""

from __future__ import annotations

import dataclasses
import datetime
import math
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from src.data.models import Base


class TrainRun(Base):
    __tablename__ = "train_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    stock_code: Mapped[str] = mapped_column(Text, nullable=False)
    granularity: Mapped[str] = mapped_column(String(10), nullable=False)
    start_dt: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_dt: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_combinations: Mapped[int] = mapped_column(Integer, nullable=False)
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False)
    config: Mapped[str | None] = mapped_column(String(500), nullable=True)

    results: Mapped[list["TrainBestResult"]] = relationship(
        "TrainBestResult", back_populates="train_run", order_by="TrainBestResult.rank"
    )


class TrainBestResult(Base):
    __tablename__ = "train_best_results"
    __table_args__ = (
        Index("ix_train_best_results_run_rank", "train_run_id", "rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    train_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("train_runs.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    total_return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    annualized_return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False)
    win_rate_pct: Mapped[float] = mapped_column(Float, nullable=False)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)  # NULL → ∞
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_holding_days: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    equity_curve: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    bar_dts: Mapped[list[str]] = mapped_column(JSONB, nullable=False)   # ISO strings
    trades_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    train_run: Mapped["TrainRun"] = relationship("TrainRun", back_populates="results")

    @property
    def profit_factor_val(self) -> float:
        return float("inf") if self.profit_factor is None else self.profit_factor


def save_best_to_db(
    session: Session,
    strategy_name: str,
    stock_code: str,
    granularity: str,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    results: list[Any],  # list[TrainResult[P]]
    top_n: int,
    initial_capital: float,
    config: str | None = None,
) -> int:
    """Persist the top *top_n* ranked results.  Returns the new train_run.id."""
    run = TrainRun(
        strategy_name=strategy_name,
        stock_code=stock_code,
        granularity=granularity,
        start_dt=start_dt,
        end_dt=end_dt,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        total_combinations=len(results),
        initial_capital=initial_capital,
        config=config,
    )
    session.add(run)
    session.flush()  # obtain run.id before inserting children

    for rank, r in enumerate(results[:top_n], 1):
        m = r.metrics
        session.add(
            TrainBestResult(
                train_run_id=run.id,
                rank=rank,
                params_json=_serialize_params(r.params),
                total_return_pct=m.total_return_pct,
                annualized_return_pct=m.annualized_return_pct,
                sharpe_ratio=m.sharpe_ratio,
                max_drawdown_pct=m.max_drawdown_pct,
                win_rate_pct=m.win_rate_pct,
                profit_factor=None if math.isinf(m.profit_factor) else m.profit_factor,
                total_trades=m.total_trades,
                avg_holding_days=m.avg_holding_days,
                score=m.score,
                equity_curve=r.result.equity_curve,
                bar_dts=[dt.isoformat() for dt in r.result.bar_dts],
                trades_json=[
                    {
                        "order_id": t.order_id,
                        "side": int(t.side),
                        "quantity": t.quantity,
                        "price": t.price,
                        "dt": t.dt.isoformat(),
                        "realized_pnl": t.realized_pnl,
                    }
                    for t in r.result.trades
                ],
            )
        )

    session.flush()
    return run.id


def _serialize_params(params: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(params) and not isinstance(params, type):
        return dataclasses.asdict(params)
    return dict(params)
