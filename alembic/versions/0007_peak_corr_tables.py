"""Peak-correlation analysis tables: peak_corr_runs, peak_corr_results.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "peak_corr_runs",
        sa.Column("id",             sa.Integer(),              autoincrement=True, nullable=False),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_dt",       sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_dt",         sa.DateTime(timezone=True), nullable=False),
        sa.Column("granularity",    sa.String(10),              nullable=False),
        sa.Column("zz_size",        sa.Integer(),               nullable=False),
        sa.Column("zz_middle_size", sa.Integer(),               nullable=False),
        sa.Column("stock_set",      sa.String(100),             nullable=True),
        sa.Column("n_indicators",   sa.Integer(),               nullable=False),
        sa.Column("n_stocks",       sa.Integer(),               nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_peak_corr_runs_created", "peak_corr_runs", ["created_at"])

    op.create_table(
        "peak_corr_results",
        sa.Column("id",          sa.Integer(),  autoincrement=True, nullable=False),
        sa.Column("run_id",      sa.Integer(),  nullable=False),
        sa.Column("stock",       sa.String(30), nullable=False),
        sa.Column("indicator",   sa.String(30), nullable=False),
        sa.Column("mean_corr_a", sa.Float(),    nullable=True),
        sa.Column("mean_corr_b", sa.Float(),    nullable=True),
        sa.Column("n_peaks",     sa.Integer(),  nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["peak_corr_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_peak_corr_results_run",       "peak_corr_results", ["run_id"])
    op.create_index("ix_peak_corr_results_stock",     "peak_corr_results", ["run_id", "stock"])
    op.create_index("ix_peak_corr_results_indicator", "peak_corr_results", ["run_id", "indicator"])


def downgrade() -> None:
    op.drop_index("ix_peak_corr_results_indicator", table_name="peak_corr_results")
    op.drop_index("ix_peak_corr_results_stock",     table_name="peak_corr_results")
    op.drop_index("ix_peak_corr_results_run",       table_name="peak_corr_results")
    op.drop_table("peak_corr_results")
    op.drop_index("ix_peak_corr_runs_created", table_name="peak_corr_runs")
    op.drop_table("peak_corr_runs")
