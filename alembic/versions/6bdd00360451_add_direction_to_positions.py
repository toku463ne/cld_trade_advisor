"""add_direction_to_positions

Revision ID: 6bdd00360451
Revises: 3ce06b6696f5
Create Date: 2026-05-09 20:26:48.980838

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '6bdd00360451'
down_revision: Union[str, Sequence[str], None] = '3ce06b6696f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('positions', sa.Column('direction', sa.String(length=5), nullable=False,
                                         server_default='long'))


def downgrade() -> None:
    op.drop_column('positions', 'direction')
