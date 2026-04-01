"""Add tasks.resolved_at

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-03-30

Tracks when a task was marked as resolved, so automations can
determine how long ago a particular type of work was last completed.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e7f8a9b0c1d2'
down_revision = 'd6e7f8a9b0c1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('resolved_at', sa.DateTime(), nullable=True))
    # Backfill: set resolved_at = updated_at for already-resolved tasks
    op.execute(
        "UPDATE tasks SET resolved_at = updated_at "
        "WHERE task_status = 'resolved' AND resolved_at IS NULL"
    )


def downgrade():
    op.drop_column('tasks', 'resolved_at')
