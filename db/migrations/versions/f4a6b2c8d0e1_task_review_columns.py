"""add task review columns — last_reviewed_at, status, summary, next_step

Revision ID: f4a6b2c8d0e1
Revises: e3f5a1b2c4d6
Create Date: 2026-04-24 05:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f4a6b2c8d0e1"
down_revision: Union[str, Sequence[str], None] = "e3f5a1b2c4d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("last_reviewed_at", sa.DateTime(), nullable=True))
    op.add_column("tasks", sa.Column("last_review_status", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("last_review_summary", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("last_review_next_step", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "last_review_next_step")
    op.drop_column("tasks", "last_review_summary")
    op.drop_column("tasks", "last_review_status")
    op.drop_column("tasks", "last_reviewed_at")
