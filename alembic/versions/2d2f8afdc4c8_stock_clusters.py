"""stock_clusters

Revision ID: 2d2f8afdc4c8
Revises: 0009
Create Date: 2026-05-05 11:49:33.568790

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2d2f8afdc4c8'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'stock_cluster_runs',
        sa.Column('id',          sa.Integer(),     primary_key=True, autoincrement=True, nullable=False),
        sa.Column('fiscal_year', sa.String(20),    nullable=False),
        sa.Column('start_dt',    sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_dt',      sa.DateTime(timezone=True), nullable=False),
        sa.Column('corr_run_id', sa.Integer(),     nullable=True),
        sa.Column('threshold',   sa.Float(),        nullable=False),
        sa.Column('n_stocks',    sa.Integer(),     nullable=False),
        sa.Column('n_clusters',  sa.Integer(),     nullable=False),
        sa.Column('created_at',  sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['corr_run_id'], ['corr_runs.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('fiscal_year', name='uq_cluster_runs_fiscal_year'),
    )

    op.create_table(
        'stock_cluster_members',
        sa.Column('id',                sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('run_id',            sa.Integer(), nullable=False),
        sa.Column('fiscal_year',       sa.String(20), nullable=False),
        sa.Column('stock_code',        sa.String(30), nullable=False),
        sa.Column('cluster_id',        sa.Integer(), nullable=False),
        sa.Column('is_representative', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('total_volume',      sa.Float(),   nullable=True),
        sa.Column('n_bars',            sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['stock_cluster_runs.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('run_id', 'stock_code', name='uq_cluster_member'),
    )
    op.create_index('ix_cluster_members_run',     'stock_cluster_members', ['run_id'])
    op.create_index('ix_cluster_members_cluster', 'stock_cluster_members', ['run_id', 'cluster_id'])
    op.create_index('ix_cluster_members_fiscal',  'stock_cluster_members', ['run_id', 'fiscal_year'])


def downgrade() -> None:
    op.drop_index('ix_cluster_members_fiscal',  table_name='stock_cluster_members')
    op.drop_index('ix_cluster_members_cluster', table_name='stock_cluster_members')
    op.drop_index('ix_cluster_members_run',     table_name='stock_cluster_members')
    op.drop_table('stock_cluster_members')
    op.drop_table('stock_cluster_runs')
