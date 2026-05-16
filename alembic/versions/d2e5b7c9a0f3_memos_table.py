"""memos table — free-form daily memo capture for Ideas sub-tab

Adds the `memos` table. Each row is an operator-written note tied to a
calendar date; the Ideas sub-tab lists them and links back to the
Daily tab for that date.

Revision ID: d2e5b7c9a0f3
Revises: c8f1d4a9e1b2
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2e5b7c9a0f3'
down_revision: Union[str, Sequence[str], None] = 'c8f1d4a9e1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'memos',
        sa.Column('id',         sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('memo_date',  sa.Date(),       nullable=False),
        sa.Column('content',    sa.Text(),       nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_memos_memo_date', 'memos', ['memo_date'])


def downgrade() -> None:
    op.drop_index('ix_memos_memo_date', table_name='memos')
    op.drop_table('memos')
