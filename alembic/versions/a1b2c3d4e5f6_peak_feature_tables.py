"""peak_feature_tables

Revision ID: a1b2c3d4e5f6
Revises: b3e9f1a2c4d5
Create Date: 2026-05-06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "b3e9f1a2c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "peak_feature_runs",
        sa.Column("id",             sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("stock_set",      sa.String(64),  nullable=False),
        sa.Column("start_dt",       sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_dt",         sa.DateTime(timezone=True), nullable=False),
        sa.Column("zz_size",        sa.Integer(), nullable=False),
        sa.Column("zz_mid_size",    sa.Integer(), nullable=False),
        sa.Column("trend_cap_days", sa.Integer(), nullable=False),
        sa.Column("n_stocks",       sa.Integer(), nullable=False),
        sa.Column("n_records",      sa.Integer(), nullable=False),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "peak_feature_records",
        sa.Column("id",             sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id",         sa.Integer(),
                  sa.ForeignKey("peak_feature_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stock_code",     sa.String(30), nullable=False),
        sa.Column("confirmed_at",   sa.DateTime(timezone=True), nullable=False),
        sa.Column("peak_at",        sa.DateTime(timezone=True), nullable=False),
        sa.Column("peak_direction", sa.Integer(), nullable=False),
        sa.Column("peak_price",     sa.Float(),   nullable=False),
        # Technical context
        sa.Column("sma20_dist",      sa.Float(), nullable=True),
        sa.Column("rsi14",           sa.Float(), nullable=True),
        sa.Column("bb_pct_b",        sa.Float(), nullable=True),
        sa.Column("vol_ratio",       sa.Float(), nullable=True),
        sa.Column("trend_age_bars",  sa.Integer(), nullable=True),
        # Market regime
        sa.Column("n225_sma20_dist", sa.Float(), nullable=True),
        sa.Column("n225_20d_ret",    sa.Float(), nullable=True),
        sa.Column("is_crash",        sa.Boolean(), nullable=True),
        # Daily correlations
        sa.Column("corr_n225", sa.Float(), nullable=True),
        sa.Column("corr_gspc", sa.Float(), nullable=True),
        sa.Column("corr_hsi",  sa.Float(), nullable=True),
        # Sign scores
        sa.Column("sign_div_bar",    sa.Float(), nullable=True),
        sa.Column("sign_div_vol",    sa.Float(), nullable=True),
        sa.Column("sign_div_gap",    sa.Float(), nullable=True),
        sa.Column("sign_div_peer",   sa.Float(), nullable=True),
        sa.Column("sign_corr_flip",  sa.Float(), nullable=True),
        sa.Column("sign_corr_shift", sa.Float(), nullable=True),
        sa.Column("sign_corr_peak",  sa.Float(), nullable=True),
        sa.Column("sign_str_hold",   sa.Float(), nullable=True),
        sa.Column("sign_str_lead",   sa.Float(), nullable=True),
        sa.Column("sign_brk_sma",    sa.Float(), nullable=True),
        sa.Column("sign_brk_bol",    sa.Float(), nullable=True),
        sa.Column("sign_rev_lo",     sa.Float(), nullable=True),
        sa.Column("sign_rev_hi",     sa.Float(), nullable=True),
        sa.Column("sign_rev_nhi",    sa.Float(), nullable=True),
        sa.Column("sign_rev_nlo",    sa.Float(), nullable=True),
        sa.Column("sign_active_count", sa.Integer(), nullable=False, server_default="0"),
        # Outcome
        sa.Column("outcome_direction", sa.Integer(), nullable=True),
        sa.Column("outcome_bars",      sa.Integer(), nullable=True),
        sa.Column("outcome_magnitude", sa.Float(),   nullable=True),
    )
    op.create_index("ix_pfr_run",      "peak_feature_records", ["run_id"])
    op.create_index("ix_pfr_stock",    "peak_feature_records", ["run_id", "stock_code"])
    op.create_index("ix_pfr_peak_dir", "peak_feature_records", ["run_id", "peak_direction"])


def downgrade() -> None:
    op.drop_index("ix_pfr_peak_dir", table_name="peak_feature_records")
    op.drop_index("ix_pfr_stock",    table_name="peak_feature_records")
    op.drop_index("ix_pfr_run",      table_name="peak_feature_records")
    op.drop_table("peak_feature_records")
    op.drop_table("peak_feature_runs")
