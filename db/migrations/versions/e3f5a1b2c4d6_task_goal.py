"""add task.goal column — manager-authored intent statement

Revision ID: e3f5a1b2c4d6
Revises: d2e3f4a5b6c7
Create Date: 2026-04-24 02:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e3f5a1b2c4d6"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("goal", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "goal")
