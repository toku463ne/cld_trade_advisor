"""trade_date column + decision unique constraint on reviewed_candidates

Adds `trade_date` (Date, NOT NULL) — the session day on which the
operator considered the fire.  Distinct from `fired_at` because the
same fire can be reviewed across multiple session days (skip today,
register tomorrow), and each session-day decision is its own upsert
row.

Adds a unique constraint on (account_id, stock_code, fired_at,
trade_date, sign_type) so Register / Skip flows upsert rather than
spawn duplicate rows on every click.

Existing rows are backfilled with `trade_date = fired_at` — the
implicit assumption before this change.

Revision ID: a8b5d2e7f1c4
Revises: f7c8e0d3b1a5
Create Date: 2026-05-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8b5d2e7f1c4'
down_revision: Union[str, Sequence[str], None] = 'f7c8e0d3b1a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add column nullable so existing rows can stay.
    op.add_column(
        'reviewed_candidates',
        sa.Column('trade_date', sa.Date(), nullable=True),
    )
    # 2. Backfill trade_date = fired_at for existing rows.
    op.execute(
        "UPDATE reviewed_candidates SET trade_date = fired_at "
        "WHERE trade_date IS NULL"
    )
    # 3. Set NOT NULL now that all rows have a value.
    op.alter_column(
        'reviewed_candidates', 'trade_date', nullable=False
    )
    # 4. Index for selection-time lookups.
    op.create_index(
        'ix_reviewed_candidates_trade_date',
        'reviewed_candidates',
        ['trade_date'],
    )
    # 5. Dedupe pre-existing rows that violate the new decision tuple.
    # Pre-column behavior allowed multiple rows for the same
    # (account, stock, fired, sign) — one Skip-then-Register flow created
    # an action=taken row alongside the earlier action=skipped row, and
    # repeated Skips created stacked rows.  The new upsert key requires
    # at most one row per (account, stock, fired_at, trade_date, sign).
    # Keep the newest row per group (max reviewed_at), discard the rest.
    op.execute(
        """
        DELETE FROM reviewed_candidates
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY account_id, stock_code, fired_at,
                                 trade_date, sign_type
                    ORDER BY reviewed_at DESC, id DESC
                ) AS rn
                FROM reviewed_candidates
            ) ranked
            WHERE rn > 1
        )
        """
    )
    # 6. Decision-level unique constraint backing the upsert.
    op.create_unique_constraint(
        'uq_reviewed_candidates_decision',
        'reviewed_candidates',
        ['account_id', 'stock_code', 'fired_at', 'trade_date', 'sign_type'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_reviewed_candidates_decision',
        'reviewed_candidates',
        type_='unique',
    )
    op.drop_index(
        'ix_reviewed_candidates_trade_date',
        table_name='reviewed_candidates',
    )
    op.drop_column('reviewed_candidates', 'trade_date')
