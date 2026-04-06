"""Add steps column to tasks

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-04-06

Ordered list of progress steps for a task, managed by the agent.
Schema: [{"key": "...", "label": "...", "status": "done|active|pending", "note": "..."}]
"""
from alembic import op
import sqlalchemy as sa

revision = 'f8a9b0c1d2e3'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('steps', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('tasks', 'steps')
