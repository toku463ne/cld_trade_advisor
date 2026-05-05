"""ORM models for stock correlation analysis."""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.data.models import Base


class CorrRun(Base):
    __tablename__ = "corr_runs"

    id:          Mapped[int]              = mapped_column(Integer, primary_key=True, autoincrement=True)
    start_dt:    Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_dt:      Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granularity: Mapped[str]              = mapped_column(String(10), nullable=False)
    window_days: Mapped[int]              = mapped_column(Integer, nullable=False)
    step_days:   Mapped[int]              = mapped_column(Integer, nullable=False)
    n_stocks:    Mapped[int]              = mapped_column(Integer, nullable=False)
    n_windows:   Mapped[int]              = mapped_column(Integer, nullable=False)
    created_at:  Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pairs: Mapped[list["StockCorrPair"]] = relationship(
        "StockCorrPair", back_populates="corr_run", cascade="all, delete-orphan"
    )


class StockCorrPair(Base):
    __tablename__ = "stock_corr_pairs"
    __table_args__ = (
        Index("ix_corr_pairs_run",     "corr_run_id"),
        Index("ix_corr_pairs_stock_a", "corr_run_id", "stock_a"),
        Index("ix_corr_pairs_stock_b", "corr_run_id", "stock_b"),
        Index("ix_corr_pairs_mean",    "corr_run_id", "mean_corr"),
    )

    id:          Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    corr_run_id: Mapped[int]   = mapped_column(Integer, ForeignKey("corr_runs.id", ondelete="CASCADE"), nullable=False)
    stock_a:     Mapped[str]   = mapped_column(String(30), nullable=False)
    stock_b:     Mapped[str]   = mapped_column(String(30), nullable=False)
    mean_corr:   Mapped[float] = mapped_column(Float, nullable=False)
    std_corr:    Mapped[float] = mapped_column(Float, nullable=False)
    n_windows:   Mapped[int]   = mapped_column(Integer, nullable=False)

    corr_run: Mapped["CorrRun"] = relationship("CorrRun", back_populates="pairs")


# ── Peak-correlation models ────────────────────────────────────────────────────


class PeakCorrRun(Base):
    __tablename__ = "peak_corr_runs"

    id:             Mapped[int]               = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at:     Mapped[datetime.datetime]  = mapped_column(DateTime(timezone=True), nullable=False)
    start_dt:       Mapped[datetime.datetime]  = mapped_column(DateTime(timezone=True), nullable=False)
    end_dt:         Mapped[datetime.datetime]  = mapped_column(DateTime(timezone=True), nullable=False)
    granularity:    Mapped[str]               = mapped_column(String(10),  nullable=False)
    zz_size:        Mapped[int]               = mapped_column(Integer,     nullable=False)
    zz_middle_size: Mapped[int]               = mapped_column(Integer,     nullable=False)
    stock_set:      Mapped[str | None]        = mapped_column(String(100), nullable=True)
    n_indicators:   Mapped[int]               = mapped_column(Integer,     nullable=False)
    n_stocks:       Mapped[int]               = mapped_column(Integer,     nullable=False)

    results: Mapped[list["PeakCorrResult"]] = relationship(
        "PeakCorrResult", back_populates="run", cascade="all, delete-orphan"
    )


class PeakCorrResult(Base):
    __tablename__ = "peak_corr_results"
    __table_args__ = (
        Index("ix_peak_corr_results_run",       "run_id"),
        Index("ix_peak_corr_results_stock",     "run_id", "stock"),
        Index("ix_peak_corr_results_indicator", "run_id", "indicator"),
    )

    id:          Mapped[int]        = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:      Mapped[int]        = mapped_column(Integer, ForeignKey("peak_corr_runs.id", ondelete="CASCADE"), nullable=False)
    stock:       Mapped[str]        = mapped_column(String(30), nullable=False)
    indicator:   Mapped[str]        = mapped_column(String(30), nullable=False)
    mean_corr_a: Mapped[float|None] = mapped_column(Float, nullable=True)
    mean_corr_b: Mapped[float|None] = mapped_column(Float, nullable=True)
    n_peaks:     Mapped[int]        = mapped_column(Integer, nullable=False)

    run: Mapped["PeakCorrRun"] = relationship("PeakCorrRun", back_populates="results")


# ── Moving-correlation model ───────────────────────────────────────────────────


class MovingCorr(Base):
    """Per-bar rolling return-correlation between a stock and a major indicator."""

    __tablename__ = "moving_corr"
    __table_args__ = (
        UniqueConstraint("stock_code", "indicator", "granularity", "window_bars", "ts",
                         name="uq_moving_corr"),
        Index("ix_moving_corr_lookup", "stock_code", "indicator", "granularity", "window_bars"),
        Index("ix_moving_corr_ts",     "ts"),
    )

    id:          Mapped[int]               = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code:  Mapped[str]               = mapped_column(String(30), nullable=False)
    indicator:   Mapped[str]               = mapped_column(String(30), nullable=False)
    granularity: Mapped[str]               = mapped_column(String(10), nullable=False)
    window_bars: Mapped[int]               = mapped_column(Integer,    nullable=False)
    ts:          Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    corr_value:  Mapped[float | None]      = mapped_column(Float,      nullable=True)


# ── Stock-cluster models ───────────────────────────────────────────────────────


class StockClusterRun(Base):
    """One clustering execution covering a fiscal-year period."""

    __tablename__ = "stock_cluster_runs"
    __table_args__ = (
        UniqueConstraint("fiscal_year", name="uq_cluster_runs_fiscal_year"),
    )

    id:          Mapped[int]               = mapped_column(Integer, primary_key=True, autoincrement=True)
    fiscal_year: Mapped[str]               = mapped_column(String(20),  nullable=False)  # e.g. "classified2023"
    start_dt:    Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_dt:      Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    corr_run_id: Mapped[int | None]        = mapped_column(Integer, ForeignKey("corr_runs.id", ondelete="SET NULL"), nullable=True)
    threshold:   Mapped[float]             = mapped_column(Float,       nullable=False)
    n_stocks:    Mapped[int]               = mapped_column(Integer,     nullable=False)
    n_clusters:  Mapped[int]               = mapped_column(Integer,     nullable=False)
    created_at:  Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    members: Mapped[list["StockClusterMember"]] = relationship(
        "StockClusterMember", back_populates="run", cascade="all, delete-orphan"
    )


class StockClusterMember(Base):
    """Cluster assignment for one stock in one StockClusterRun."""

    __tablename__ = "stock_cluster_members"
    __table_args__ = (
        UniqueConstraint("run_id", "stock_code", name="uq_cluster_member"),
        Index("ix_cluster_members_run",        "run_id"),
        Index("ix_cluster_members_cluster",    "run_id", "cluster_id"),
        Index("ix_cluster_members_fiscal",     "run_id", "fiscal_year"),
    )

    id:               Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:           Mapped[int]  = mapped_column(Integer, ForeignKey("stock_cluster_runs.id", ondelete="CASCADE"), nullable=False)
    fiscal_year:      Mapped[str]  = mapped_column(String(20),  nullable=False)
    stock_code:       Mapped[str]  = mapped_column(String(30),  nullable=False)
    cluster_id:       Mapped[int]  = mapped_column(Integer,     nullable=False)
    is_representative: Mapped[bool] = mapped_column(Boolean,   nullable=False, default=False)
    total_volume:     Mapped[float | None] = mapped_column(Float, nullable=True)
    n_bars:           Mapped[int | None]   = mapped_column(Integer, nullable=True)

    run: Mapped["StockClusterRun"] = relationship("StockClusterRun", back_populates="members")
