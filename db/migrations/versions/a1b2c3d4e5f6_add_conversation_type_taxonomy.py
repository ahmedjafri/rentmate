"""add conversation_type taxonomy

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-03-28

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversations', sa.Column('conversation_type', sa.String(20), nullable=True))
    op.add_column('conversations', sa.Column('parent_conversation_id', sa.String(36), sa.ForeignKey('conversations.id'), nullable=True))
    op.add_column('conversations', sa.Column('ancestor_ids', sa.JSON, nullable=True, server_default='[]'))
    op.add_column('conversations', sa.Column('ai_initiated', sa.Boolean, nullable=False, server_default='false'))

    # Backfill conversation_type
    op.execute("UPDATE conversations SET conversation_type = 'task' WHERE is_task = TRUE")
    op.execute("""
        UPDATE conversations SET conversation_type = 'tenant'
        WHERE is_task = FALSE AND id IN (
            SELECT DISTINCT conversation_id FROM conversation_participants WHERE participant_type = 'tenant'
        )
    """)
    op.execute("""
        UPDATE conversations SET conversation_type = 'vendor'
        WHERE is_task = FALSE AND conversation_type IS NULL AND id IN (
            SELECT DISTINCT conversation_id FROM conversation_participants WHERE participant_type = 'external_contact'
        )
    """)
    op.execute("UPDATE conversations SET conversation_type = 'user_ai' WHERE conversation_type IS NULL")
    op.alter_column('conversations', 'conversation_type', nullable=False)


def downgrade():
    op.drop_column('conversations', 'ai_initiated')
    op.drop_column('conversations', 'ancestor_ids')
    op.drop_column('conversations', 'parent_conversation_id')
    op.drop_column('conversations', 'conversation_type')
