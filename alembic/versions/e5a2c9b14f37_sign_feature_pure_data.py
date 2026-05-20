"""sign_feature_records: drop a-priori directional counts, add n225_valid_n

Make the table pure observed data. The bullish/bearish grouping was an a-priori
sign-design assumption (discover data shows ~8 of the labels disagree with
measured forward returns), so directionality must be DERIVED from the table, not
stored in it. Drop bullish_valid_n / bearish_valid_n / n225_bullish_n /
n225_bearish_n; add the direction-agnostic n225_valid_n. Raw per-sign scores
remain in cofire_scores / n225_scores (JSONB), so nothing is lost.

Revision ID: e5a2c9b14f37
Revises: d3f9a1c7e2b8
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a2c9b14f37'
down_revision: Union[str, Sequence[str], None] = 'd3f9a1c7e2b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sign_feature_records',
                  sa.Column('n225_valid_n', sa.Integer(), nullable=True))
    op.drop_column('sign_feature_records', 'bullish_valid_n')
    op.drop_column('sign_feature_records', 'bearish_valid_n')
    op.drop_column('sign_feature_records', 'n225_bullish_n')
    op.drop_column('sign_feature_records', 'n225_bearish_n')


def downgrade() -> None:
    op.add_column('sign_feature_records',
                  sa.Column('n225_bearish_n', sa.Integer(), nullable=True))
    op.add_column('sign_feature_records',
                  sa.Column('n225_bullish_n', sa.Integer(), nullable=True))
    op.add_column('sign_feature_records',
                  sa.Column('bearish_valid_n', sa.Integer(), nullable=True))
    op.add_column('sign_feature_records',
                  sa.Column('bullish_valid_n', sa.Integer(), nullable=True))
    op.drop_column('sign_feature_records', 'n225_valid_n')
