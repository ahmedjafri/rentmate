"""Add tasks.external_conversation_id

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-03-29

Adds a direct FK from tasks to the active vendor/tenant chat thread.
Unlike ai_conversation_id, this conversation is NOT deleted when the
task is deleted — it belongs to the external party.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd6e7f8a9b0c1'
down_revision = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('external_conversation_id', sa.String(36), nullable=True))
    op.create_foreign_key(
        'fk_tasks_external_convo', 'tasks', 'conversations',
        ['external_conversation_id'], ['id'], ondelete='SET NULL',
    )


def downgrade():
    op.drop_constraint('fk_tasks_external_convo', 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'external_conversation_id')
