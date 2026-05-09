"""add_positions_table

Revision ID: 3ce06b6696f5
Revises: f1g2h3i4j5k6
Create Date: 2026-05-09 14:47:50.949596

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3ce06b6696f5'
down_revision: Union[str, Sequence[str], None] = 'f1g2h3i4j5k6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'positions',
        sa.Column('id',           sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('stock_code',   sa.String(length=20),                nullable=False),
        sa.Column('sign_type',    sa.String(length=30),                nullable=False),
        sa.Column('corr_mode',    sa.String(length=10),                nullable=False),
        sa.Column('kumo_state',   sa.Integer(),                        nullable=False),
        sa.Column('fired_at',     sa.Date(),                           nullable=False),
        sa.Column('entry_date',   sa.Date(),                           nullable=False),
        sa.Column('entry_price',  sa.Numeric(precision=12, scale=2),   nullable=False),
        sa.Column('units',        sa.Integer(),                        nullable=False),
        sa.Column('tp_price',     sa.Numeric(precision=12, scale=2),   nullable=True),
        sa.Column('sl_price',     sa.Numeric(precision=12, scale=2),   nullable=True),
        sa.Column('status',       sa.String(length=10),                nullable=False),
        sa.Column('exit_date',    sa.Date(),                           nullable=True),
        sa.Column('exit_price',   sa.Numeric(precision=12, scale=2),   nullable=True),
        sa.Column('notes',        sa.Text(),                           nullable=True),
        sa.Column('created_at',   sa.DateTime(timezone=True),          nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('positions')
