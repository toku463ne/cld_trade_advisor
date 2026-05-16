"""tags column on reviewed_candidates

Adds a nullable comma-separated `tags` text column on
`reviewed_candidates` so the operator can categorize decisions and
surface past tags in the UI for quick re-selection on future
decisions.

Revision ID: f7c8e0d3b1a5
Revises: e6a3b8d2c1f4
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7c8e0d3b1a5'
down_revision: Union[str, Sequence[str], None] = 'e6a3b8d2c1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'reviewed_candidates',
        sa.Column('tags', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('reviewed_candidates', 'tags')
