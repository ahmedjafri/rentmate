"""add progress column to documents

Revision ID: d5e8f3a2b1c4
Revises: c4d7e2f1a8b9
Create Date: 2026-03-07 00:02:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd5e8f3a2b1c4'
down_revision: Union[str, Sequence[str], None] = 'c4d7e2f1a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('progress', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('documents', 'progress')
