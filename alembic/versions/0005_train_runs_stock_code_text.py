"""Widen train_runs.stock_code from VARCHAR(20) to TEXT.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "train_runs",
        "stock_code",
        type_=sa.Text(),
        existing_type=sa.String(20),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "train_runs",
        "stock_code",
        type_=sa.String(20),
        existing_type=sa.Text(),
        existing_nullable=False,
    )
