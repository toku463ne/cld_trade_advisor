"""position levels_updated_at

Revision ID: 2ad6c7bb07fc
Revises: a1c4e7f93b2d
Create Date: 2026-07-21 20:29:06.797454

Autogenerate also emitted drop_table for every ohlcv_1d_yXXXX partition and
NOT NULL alters on jq_* tables — both are pre-existing schema drift unrelated
to this change (see CLAUDE.md "DB Schema Changes"). Removed by hand; this
migration adds one nullable column and nothing else.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2ad6c7bb07fc'
down_revision: Union[str, Sequence[str], None] = 'a1c4e7f93b2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add positions.levels_updated_at."""
    op.add_column('positions', sa.Column('levels_updated_at', sa.Date(), nullable=True))


def downgrade() -> None:
    """Drop positions.levels_updated_at."""
    op.drop_column('positions', 'levels_updated_at')
