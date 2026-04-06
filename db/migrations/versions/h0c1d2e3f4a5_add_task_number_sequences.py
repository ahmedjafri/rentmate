"""Add task_number_sequences table

Revision ID: h0c1d2e3f4a5
Revises: g9b0c1d2e3f4
Create Date: 2026-04-06

Monotonically increasing per-account counter for task numbers so that
numbers are never reused after a task is deleted.
"""
from alembic import op
import sqlalchemy as sa

revision = 'h0c1d2e3f4a5'
down_revision = 'g9b0c1d2e3f4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'task_number_sequences',
        sa.Column('account_id', sa.String(36), primary_key=True),
        sa.Column('last_number', sa.Integer, nullable=False, server_default='0'),
    )
    # Back-fill from existing tasks so the sequence picks up where each
    # account left off (including any already-deleted task numbers).
    op.execute("""
        INSERT INTO task_number_sequences (account_id, last_number)
        SELECT account_id, COALESCE(MAX(task_number), 0)
        FROM tasks
        GROUP BY account_id
    """)


def downgrade():
    op.drop_table('task_number_sequences')
