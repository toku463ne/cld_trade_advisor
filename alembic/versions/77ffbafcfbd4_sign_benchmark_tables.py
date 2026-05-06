"""sign_benchmark_tables

Revision ID: 77ffbafcfbd4
Revises: 2d2f8afdc4c8
Create Date: 2026-05-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '77ffbafcfbd4'
down_revision: Union[str, Sequence[str], None] = '2d2f8afdc4c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on:    Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sign_benchmark_runs",
        sa.Column("id",            sa.Integer(),                       nullable=False),
        sa.Column("sign_type",     sa.String(32),                      nullable=False),
        sa.Column("stock_set",     sa.String(64),                      nullable=False),
        sa.Column("gran",          sa.String(10),                      nullable=False),
        sa.Column("start_dt",      sa.DateTime(timezone=True),         nullable=False),
        sa.Column("end_dt",        sa.DateTime(timezone=True),         nullable=False),
        sa.Column("window",        sa.Integer(),                       nullable=False),
        sa.Column("valid_bars",    sa.Integer(),                       nullable=False),
        sa.Column("fwd_days",      sa.Integer(),                       nullable=False),
        sa.Column("tp_pct",        sa.Float(),                         nullable=False),
        sa.Column("sl_pct",        sa.Float(),                         nullable=False),
        sa.Column("max_hold_days", sa.Integer(),                       nullable=False),
        sa.Column("n_stocks",      sa.Integer(),                       nullable=False),
        sa.Column("n_events",      sa.Integer(),                       nullable=False),
        sa.Column("win_rate",           sa.Float(),                    nullable=True),
        sa.Column("mean_fwd_return",    sa.Float(),                    nullable=True),
        sa.Column("mean_excess_return", sa.Float(),                    nullable=True),
        sa.Column("trade_win_rate",     sa.Float(),                    nullable=True),
        sa.Column("mean_trade_pnl",     sa.Float(),                    nullable=True),
        sa.Column("ic",                 sa.Float(),                    nullable=True),
        sa.Column("median_trend_days",  sa.Float(),                    nullable=True),
        sa.Column("created_at",    sa.DateTime(timezone=True),         nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sbr_sign_set", "sign_benchmark_runs", ["sign_type", "stock_set"])
    op.create_index("ix_sbr_period",   "sign_benchmark_runs", ["start_dt",  "end_dt"])

    op.create_table(
        "sign_benchmark_events",
        sa.Column("id",            sa.Integer(),               nullable=False),
        sa.Column("run_id",        sa.Integer(),               nullable=False),
        sa.Column("stock_code",    sa.String(30),              nullable=False),
        sa.Column("fired_at",      sa.DateTime(timezone=True), nullable=False),
        sa.Column("sign_score",    sa.Float(),                 nullable=False),
        sa.Column("entry_price",   sa.Float(),                 nullable=True),
        sa.Column("fwd_return",    sa.Float(),                 nullable=True),
        sa.Column("excess_return", sa.Float(),                 nullable=True),
        sa.Column("trade_outcome", sa.String(10),              nullable=True),
        sa.Column("trade_pnl",     sa.Float(),                 nullable=True),
        sa.Column("trend_days",    sa.Integer(),               nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["sign_benchmark_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sbe_run",   "sign_benchmark_events", ["run_id"])
    op.create_index("ix_sbe_stock", "sign_benchmark_events", ["run_id", "stock_code"])


def downgrade() -> None:
    op.drop_index("ix_sbe_stock", table_name="sign_benchmark_events")
    op.drop_index("ix_sbe_run",   table_name="sign_benchmark_events")
    op.drop_table("sign_benchmark_events")
    op.drop_index("ix_sbr_period",   table_name="sign_benchmark_runs")
    op.drop_index("ix_sbr_sign_set", table_name="sign_benchmark_runs")
    op.drop_table("sign_benchmark_runs")
