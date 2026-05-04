"""ORM models for stock correlation analysis."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
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
