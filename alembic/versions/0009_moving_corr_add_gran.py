"""moving_corr: rename window_days → window_bars, add granularity column.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_moving_corr_lookup", table_name="moving_corr")
    op.drop_constraint("uq_moving_corr", "moving_corr", type_="unique")

    op.alter_column("moving_corr", "window_days", new_column_name="window_bars")
    op.add_column("moving_corr",
                  sa.Column("granularity", sa.String(10), nullable=False,
                            server_default="1d"))
    # Remove the server default after back-filling existing rows
    op.alter_column("moving_corr", "granularity", server_default=None)

    op.create_unique_constraint(
        "uq_moving_corr", "moving_corr",
        ["stock_code", "indicator", "granularity", "window_bars", "ts"],
    )
    op.create_index("ix_moving_corr_lookup", "moving_corr",
                    ["stock_code", "indicator", "granularity", "window_bars"])


def downgrade() -> None:
    op.drop_index("ix_moving_corr_lookup", table_name="moving_corr")
    op.drop_constraint("uq_moving_corr", "moving_corr", type_="unique")

    op.drop_column("moving_corr", "granularity")
    op.alter_column("moving_corr", "window_bars", new_column_name="window_days")

    op.create_unique_constraint(
        "uq_moving_corr", "moving_corr",
        ["stock_code", "indicator", "window_days", "ts"],
    )
    op.create_index("ix_moving_corr_lookup", "moving_corr",
                    ["stock_code", "indicator", "window_days"])
