"""n225_regime_snapshots

Revision ID: f1g2h3i4j5k6
Revises: a1b2c3d4e5f6
Create Date: 2026-05-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "f1g2h3i4j5k6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "n225_regime_snapshots",
        sa.Column("date",        sa.Date(),         primary_key=True),
        sa.Column("close",       sa.Float(),        nullable=False),
        sa.Column("adx",         sa.Float(),        nullable=True),
        sa.Column("adx_pos",     sa.Float(),        nullable=True),
        sa.Column("adx_neg",     sa.Float(),        nullable=True),
        sa.Column("kumo_top",    sa.Float(),        nullable=True),
        sa.Column("kumo_bottom", sa.Float(),        nullable=True),
        sa.Column("kumo_state",  sa.SmallInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("n225_regime_snapshots")
