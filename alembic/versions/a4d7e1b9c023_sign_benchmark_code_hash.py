"""sign_benchmark_code_hash

Adds a nullable `code_hash` column to `sign_benchmark_runs` so the
maintenance grid can detect when a benchmark row is stale relative
to the current sign module source.

Revision ID: a4d7e1b9c023
Revises: 6bdd00360451
Create Date: 2026-05-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a4d7e1b9c023'
down_revision: Union[str, Sequence[str], None] = '6bdd00360451'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sign_benchmark_runs',
        sa.Column('code_hash', sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('sign_benchmark_runs', 'code_hash')
