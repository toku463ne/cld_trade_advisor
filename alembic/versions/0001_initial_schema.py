"""Initial schema: stocks table and partitioned OHLCV parent tables.

Child partitions are created at runtime by src.data.partitions.ensure_partitions().

Revision ID: 0001
Revises:
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_GRANULARITIES = ("1m", "5m", "15m", "30m", "1h", "1d", "1wk")


def upgrade() -> None:
    op.create_table(
        "stocks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("market", sa.String(100), nullable=True),
        sa.Column("sector33", sa.String(200), nullable=True),
        sa.Column("sector17", sa.String(200), nullable=True),
        sa.Column("scale", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_stocks_code"),
    )

    # Create each OHLCV parent table as PARTITION BY RANGE (ts).
    # No child partitions here — they are created on demand.
    for gran in _GRANULARITIES:
        tname = f"ohlcv_{gran}"
        op.execute(
            sa.text(
                f"""
                CREATE TABLE {tname} (
                    stock_code  VARCHAR(20)              NOT NULL,
                    ts          TIMESTAMP WITH TIME ZONE NOT NULL,
                    open_price  NUMERIC(14, 4)           NOT NULL,
                    high_price  NUMERIC(14, 4)           NOT NULL,
                    low_price   NUMERIC(14, 4)           NOT NULL,
                    close_price NUMERIC(14, 4)           NOT NULL,
                    volume      BIGINT                   NOT NULL,
                    PRIMARY KEY (stock_code, ts)
                ) PARTITION BY RANGE (ts)
                """
            )
        )
        # Additional index on ts alone for partition-pruning queries
        op.execute(
            sa.text(f"CREATE INDEX ix_{tname}_ts ON {tname} (ts)")
        )


def downgrade() -> None:
    for gran in reversed(_GRANULARITIES):
        op.execute(sa.text(f"DROP TABLE IF EXISTS ohlcv_{gran} CASCADE"))
    op.drop_table("stocks")
