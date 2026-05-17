"""exit_notes column on positions

Free-form text captured when the operator clicks Close Position —
analogous to the entry-time `reason` field on reviewed_candidates.
The existing `exit_reason` column stays as the short tag
(manual / tp_hit / sl_hit / time_stop); `exit_notes` is the longer
human-written rationale.

Nullable, no backfill needed (legacy closed rows simply have NULL
exit_notes).

Revision ID: c2e8f4a9d1b6
Revises: a8b5d2e7f1c4
Create Date: 2026-05-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2e8f4a9d1b6'
down_revision: Union[str, Sequence[str], None] = 'a8b5d2e7f1c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'positions',
        sa.Column('exit_notes', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('positions', 'exit_notes')
