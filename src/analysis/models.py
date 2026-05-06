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


# ── Sign-benchmark models ─────────────────────────────────────────────────────


class SignBenchmarkRun(Base):
    """One benchmark execution: one sign type × one stock set × one period."""

    __tablename__ = "sign_benchmark_runs"
    __table_args__ = (
        Index("ix_sbr_sign_set", "sign_type", "stock_set"),
        Index("ix_sbr_period",   "start_dt",  "end_dt"),
    )

    id:             Mapped[int]               = mapped_column(Integer, primary_key=True, autoincrement=True)
    sign_type:      Mapped[str]               = mapped_column(String(32),  nullable=False)
    stock_set:      Mapped[str]               = mapped_column(String(64),  nullable=False)
    gran:           Mapped[str]               = mapped_column(String(10),  nullable=False)
    start_dt:       Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_dt:         Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Detector parameters
    window:         Mapped[int]               = mapped_column(Integer, nullable=False)
    valid_bars:     Mapped[int]               = mapped_column(Integer, nullable=False)
    # Zigzag parameters
    zz_size:        Mapped[int]               = mapped_column(Integer, nullable=False)
    zz_mid_size:    Mapped[int]               = mapped_column(Integer, nullable=False)
    trend_cap_days: Mapped[int]               = mapped_column(Integer, nullable=False)
    # Counts
    n_stocks:       Mapped[int]               = mapped_column(Integer, nullable=False)
    n_events:       Mapped[int]               = mapped_column(Integer, nullable=False)
    # Aggregate results (nullable — populated after events are saved)
    direction_rate:  Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_trend_bars: Mapped[float | None] = mapped_column(Float, nullable=True)
    mag_follow:      Mapped[float | None] = mapped_column(Float, nullable=True)
    mag_reverse:     Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_flw:   Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_rev:   Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at:      Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    events: Mapped[list["SignBenchmarkEvent"]] = relationship(
        "SignBenchmarkEvent", back_populates="run", cascade="all, delete-orphan"
    )


class SignBenchmarkEvent(Base):
    """Per-fire-event result within a SignBenchmarkRun."""

    __tablename__ = "sign_benchmark_events"
    __table_args__ = (
        Index("ix_sbe_run",   "run_id"),
        Index("ix_sbe_stock", "run_id", "stock_code"),
    )

    id:              Mapped[int]               = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:          Mapped[int]               = mapped_column(Integer, ForeignKey("sign_benchmark_runs.id", ondelete="CASCADE"), nullable=False)
    stock_code:      Mapped[str]               = mapped_column(String(30), nullable=False)
    fired_at:        Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sign_score:      Mapped[float]             = mapped_column(Float,   nullable=False)
    trend_direction: Mapped[int | None]        = mapped_column(Integer, nullable=True)   # +1=HIGH first, -1=LOW first
    trend_bars:      Mapped[int | None]        = mapped_column(Integer, nullable=True)   # bars from entry to first confirmed peak
    trend_magnitude: Mapped[float | None]      = mapped_column(Float,   nullable=True)   # |peak - entry| / entry

    run: Mapped["SignBenchmarkRun"] = relationship("SignBenchmarkRun", back_populates="events")


# ── Peak-feature models ───────────────────────────────────────────────────────


class PeakFeatureRun(Base):
    """One execution: collect peak context features for a stock set + period."""

    __tablename__ = "peak_feature_runs"

    id:             Mapped[int]               = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_set:      Mapped[str]               = mapped_column(String(64),  nullable=False)
    start_dt:       Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_dt:         Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    zz_size:        Mapped[int]               = mapped_column(Integer, nullable=False)
    zz_mid_size:    Mapped[int]               = mapped_column(Integer, nullable=False)
    trend_cap_days: Mapped[int]               = mapped_column(Integer, nullable=False)
    n_stocks:       Mapped[int]               = mapped_column(Integer, nullable=False)
    n_records:      Mapped[int]               = mapped_column(Integer, nullable=False)
    created_at:     Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    records: Mapped[list["PeakFeatureRecord"]] = relationship(
        "PeakFeatureRecord", back_populates="run", cascade="all, delete-orphan"
    )


class PeakFeatureRecord(Base):
    """Context features at each confirmed hourly zigzag peak."""

    __tablename__ = "peak_feature_records"
    __table_args__ = (
        Index("ix_pfr_run",      "run_id"),
        Index("ix_pfr_stock",    "run_id", "stock_code"),
        Index("ix_pfr_peak_dir", "run_id", "peak_direction"),
    )

    id:             Mapped[int]               = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:         Mapped[int]               = mapped_column(Integer, ForeignKey("peak_feature_runs.id", ondelete="CASCADE"), nullable=False)
    stock_code:     Mapped[str]               = mapped_column(String(30), nullable=False)
    confirmed_at:   Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    peak_at:        Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    peak_direction: Mapped[int]               = mapped_column(Integer, nullable=False)   # +2=HIGH, -2=LOW
    peak_price:     Mapped[float]             = mapped_column(Float,   nullable=False)

    # Technical context (daily-derived at confirmation date)
    sma20_dist:      Mapped[float | None] = mapped_column(Float,   nullable=True)   # (price − SMA20) / SMA20
    rsi14:           Mapped[float | None] = mapped_column(Float,   nullable=True)
    bb_pct_b:        Mapped[float | None] = mapped_column(Float,   nullable=True)   # Bollinger %B
    vol_ratio:       Mapped[float | None] = mapped_column(Float,   nullable=True)   # hourly vol / 20-bar avg
    trend_age_bars:  Mapped[int | None]   = mapped_column(Integer, nullable=True)   # hourly bars since last opposite peak

    # Market regime context
    n225_sma20_dist: Mapped[float | None] = mapped_column(Float,   nullable=True)
    n225_20d_ret:    Mapped[float | None] = mapped_column(Float,   nullable=True)   # crash detector
    is_crash:        Mapped[bool | None]  = mapped_column(Boolean, nullable=True)

    # Daily correlation (10-day rolling Pearson vs major indicators)
    corr_n225: Mapped[float | None] = mapped_column(Float, nullable=True)
    corr_gspc: Mapped[float | None] = mapped_column(Float, nullable=True)
    corr_hsi:  Mapped[float | None] = mapped_column(Float, nullable=True)

    # Sign scores — NULL = not active, float = active with this score
    sign_div_bar:    Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_div_vol:    Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_div_gap:    Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_div_peer:   Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_corr_flip:  Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_corr_shift: Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_corr_peak:  Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_str_hold:   Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_str_lead:   Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_brk_sma:    Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_brk_bol:    Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_rev_lo:     Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_rev_hi:     Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_rev_nhi:    Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_rev_nlo:    Mapped[float | None] = mapped_column(Float, nullable=True)
    sign_active_count: Mapped[int]        = mapped_column(Integer, nullable=False, default=0)

    # Outcome: first confirmed zigzag peak within trend_cap_days
    outcome_direction: Mapped[int | None]   = mapped_column(Integer, nullable=True)   # +1=HIGH, -1=LOW
    outcome_bars:      Mapped[int | None]   = mapped_column(Integer, nullable=True)
    outcome_magnitude: Mapped[float | None] = mapped_column(Float,   nullable=True)

    run: Mapped["PeakFeatureRun"] = relationship("PeakFeatureRun", back_populates="records")


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
