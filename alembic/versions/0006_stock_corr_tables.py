"""Stock correlation analysis tables: corr_runs, stock_corr_pairs.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corr_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("start_dt",    sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_dt",      sa.DateTime(timezone=True), nullable=False),
        sa.Column("granularity", sa.String(10),  nullable=False),
        sa.Column("window_days", sa.Integer(),   nullable=False),
        sa.Column("step_days",   sa.Integer(),   nullable=False),
        sa.Column("n_stocks",    sa.Integer(),   nullable=False),
        sa.Column("n_windows",   sa.Integer(),   nullable=False),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_corr_runs_created", "corr_runs", ["created_at"])

    op.create_table(
        "stock_corr_pairs",
        sa.Column("id",          sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("corr_run_id", sa.Integer(), nullable=False),
        sa.Column("stock_a",     sa.String(30), nullable=False),
        sa.Column("stock_b",     sa.String(30), nullable=False),
        sa.Column("mean_corr",   sa.Float(),   nullable=False),
        sa.Column("std_corr",    sa.Float(),   nullable=False),
        sa.Column("n_windows",   sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["corr_run_id"], ["corr_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_corr_pairs_run",      "stock_corr_pairs", ["corr_run_id"])
    op.create_index("ix_corr_pairs_stock_a",  "stock_corr_pairs", ["corr_run_id", "stock_a"])
    op.create_index("ix_corr_pairs_stock_b",  "stock_corr_pairs", ["corr_run_id", "stock_b"])
    op.create_index("ix_corr_pairs_mean",     "stock_corr_pairs", ["corr_run_id", "mean_corr"])


def downgrade() -> None:
    op.drop_index("ix_corr_pairs_mean",    table_name="stock_corr_pairs")
    op.drop_index("ix_corr_pairs_stock_b", table_name="stock_corr_pairs")
    op.drop_index("ix_corr_pairs_stock_a", table_name="stock_corr_pairs")
    op.drop_index("ix_corr_pairs_run",     table_name="stock_corr_pairs")
    op.drop_table("stock_corr_pairs")
    op.drop_index("ix_corr_runs_created", table_name="corr_runs")
    op.drop_table("corr_runs")
