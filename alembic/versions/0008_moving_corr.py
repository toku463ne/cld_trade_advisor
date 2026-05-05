"""Per-day rolling return-correlation table: moving_corr.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "moving_corr",
        sa.Column("id",          sa.Integer(),              autoincrement=True, nullable=False),
        sa.Column("stock_code",  sa.String(30),             nullable=False),
        sa.Column("indicator",   sa.String(30),             nullable=False),
        sa.Column("window_days", sa.Integer(),              nullable=False),
        sa.Column("ts",          sa.DateTime(timezone=True), nullable=False),
        sa.Column("corr_value",  sa.Float(),                nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stock_code", "indicator", "window_days", "ts",
                            name="uq_moving_corr"),
    )
    op.create_index("ix_moving_corr_lookup", "moving_corr",
                    ["stock_code", "indicator", "window_days"])
    op.create_index("ix_moving_corr_ts", "moving_corr", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_moving_corr_ts",     table_name="moving_corr")
    op.drop_index("ix_moving_corr_lookup", table_name="moving_corr")
    op.drop_table("moving_corr")
