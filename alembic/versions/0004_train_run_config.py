"""Add config column to train_runs.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "train_runs",
        sa.Column("config", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("train_runs", "config")
