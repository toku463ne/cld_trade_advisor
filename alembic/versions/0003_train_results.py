"""Training result tables: train_runs, train_best_results.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "train_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy_name", sa.String(100), nullable=False),
        sa.Column("stock_code", sa.String(20), nullable=False),
        sa.Column("granularity", sa.String(10), nullable=False),
        sa.Column("start_dt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_dt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_combinations", sa.Integer(), nullable=False),
        sa.Column("initial_capital", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_train_runs_strategy", "train_runs", ["strategy_name"])

    op.create_table(
        "train_best_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("train_run_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("params_json", JSONB(), nullable=False),
        sa.Column("total_return_pct", sa.Float(), nullable=False),
        sa.Column("annualized_return_pct", sa.Float(), nullable=False),
        sa.Column("sharpe_ratio", sa.Float(), nullable=False),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=False),
        sa.Column("win_rate_pct", sa.Float(), nullable=False),
        sa.Column("profit_factor", sa.Float(), nullable=True),  # NULL → ∞
        sa.Column("total_trades", sa.Integer(), nullable=False),
        sa.Column("avg_holding_days", sa.Float(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("equity_curve", JSONB(), nullable=False),
        sa.Column("bar_dts", JSONB(), nullable=False),
        sa.Column("trades_json", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["train_run_id"], ["train_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_train_best_results_run_rank",
        "train_best_results",
        ["train_run_id", "rank"],
    )


def downgrade() -> None:
    op.drop_index("ix_train_best_results_run_rank", table_name="train_best_results")
    op.drop_table("train_best_results")
    op.drop_index("ix_train_runs_strategy", table_name="train_runs")
    op.drop_table("train_runs")
