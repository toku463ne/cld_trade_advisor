"""reviewed_candidates table + position regime/context columns

Adds:
  - `reviewed_candidates` table — every Skip / Register click in the
    Daily-tab UI writes one row, capturing the regime snapshot and the
    operator's decision.
  - Five nullable context columns on `positions`: sign_score,
    exit_reason, revn_frac, sma_frac, corr_frac.

Written manually (not via autogenerate) to avoid the OHLCV partition
drift documented in CLAUDE.md § DB Schema Changes.

Revision ID: c8f1d4a9e1b2
Revises: a4d7e1b9c023
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8f1d4a9e1b2'
down_revision: Union[str, Sequence[str], None] = 'a4d7e1b9c023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Position context columns
    op.add_column('positions', sa.Column('exit_reason', sa.String(length=16), nullable=True))
    op.add_column('positions', sa.Column('sign_score',  sa.Float(),           nullable=True))
    op.add_column('positions', sa.Column('revn_frac',   sa.Float(),           nullable=True))
    op.add_column('positions', sa.Column('sma_frac',    sa.Float(),           nullable=True))
    op.add_column('positions', sa.Column('corr_frac',   sa.Float(),           nullable=True))

    # Reviewed candidates table
    op.create_table(
        'reviewed_candidates',
        sa.Column('id',          sa.BigInteger(),  primary_key=True, autoincrement=True),
        sa.Column('fired_at',    sa.Date(),        nullable=False),
        sa.Column('stock_code',  sa.String(20),    nullable=False),
        sa.Column('sign_type',   sa.String(30),    nullable=False),
        sa.Column('sign_score',  sa.Float(),       nullable=True),
        sa.Column('corr_mode',   sa.String(10),    nullable=True),
        sa.Column('corr_n225',   sa.Float(),       nullable=True),
        sa.Column('kumo_state',  sa.Integer(),     nullable=True),
        sa.Column('action',      sa.String(16),    nullable=False),
        sa.Column('position_id', sa.BigInteger(),  sa.ForeignKey('positions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('reason',      sa.Text(),        nullable=True),
        sa.Column('revn_frac',   sa.Float(),       nullable=True),
        sa.Column('sma_frac',    sa.Float(),       nullable=True),
        sa.Column('corr_frac',   sa.Float(),       nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index(
        'ix_reviewed_candidates_fired_at',
        'reviewed_candidates',
        ['fired_at'],
    )
    op.create_index(
        'ix_reviewed_candidates_fired_action',
        'reviewed_candidates',
        ['fired_at', 'action'],
    )


def downgrade() -> None:
    op.drop_index('ix_reviewed_candidates_fired_action', table_name='reviewed_candidates')
    op.drop_index('ix_reviewed_candidates_fired_at',     table_name='reviewed_candidates')
    op.drop_table('reviewed_candidates')

    op.drop_column('positions', 'corr_frac')
    op.drop_column('positions', 'sma_frac')
    op.drop_column('positions', 'revn_frac')
    op.drop_column('positions', 'sign_score')
    op.drop_column('positions', 'exit_reason')
