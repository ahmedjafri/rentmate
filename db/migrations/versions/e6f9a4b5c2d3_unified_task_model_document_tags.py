"""unified task model, document tags

Revision ID: e6f9a4b5c2d3
Revises: d5e8f3a2b1c4
Create Date: 2026-03-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e6f9a4b5c2d3'
down_revision: Union[str, Sequence[str], None] = 'd5e8f3a2b1c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Conversation: unified Task model columns ---
    op.add_column('conversations', sa.Column('is_task', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('conversations', sa.Column('task_status', sa.String(length=20), nullable=True))
    op.add_column('conversations', sa.Column('task_mode', sa.String(length=25), nullable=True))
    op.add_column('conversations', sa.Column('source', sa.String(length=25), nullable=True))
    op.add_column('conversations', sa.Column('category', sa.String(length=20), nullable=True))
    op.add_column('conversations', sa.Column('urgency', sa.String(length=20), nullable=True))
    op.add_column('conversations', sa.Column('priority', sa.String(length=20), nullable=True))
    op.add_column('conversations', sa.Column('confidential', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('conversations', sa.Column('last_message_at', sa.DateTime(), nullable=True))

    # --- Message: AI chat enhancement columns ---
    op.add_column('messages', sa.Column('message_type', sa.String(length=20), nullable=True))
    op.add_column('messages', sa.Column('sender_name', sa.String(length=255), nullable=True))
    op.add_column('messages', sa.Column('is_ai', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('messages', sa.Column('draft_reply', sa.Text(), nullable=True))
    op.add_column('messages', sa.Column('approval_status', sa.String(length=20), nullable=True))
    op.add_column('messages', sa.Column('related_task_ids', sa.JSON(), nullable=True))

    # --- Document: confirmed_at ---
    op.add_column('documents', sa.Column('confirmed_at', sa.DateTime(), nullable=True))

    # --- Lease: payment_status ---
    op.add_column('leases', sa.Column('payment_status', sa.String(length=20), nullable=True, server_default='current'))

    # --- New table: document_tasks ---
    op.create_table(
        'document_tasks',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('task_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'task_id', name='uq_document_task'),
    )
    op.create_index(op.f('ix_document_tasks_document_id'), 'document_tasks', ['document_id'], unique=False)
    op.create_index(op.f('ix_document_tasks_task_id'), 'document_tasks', ['task_id'], unique=False)

    # --- New table: document_tags ---
    op.create_table(
        'document_tags',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('tag_type', sa.String(length=20), nullable=False),
        sa.Column('property_id', sa.String(length=36), nullable=True),
        sa.Column('unit_id', sa.String(length=36), nullable=True),
        sa.Column('tenant_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['unit_id'], ['units.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_document_tags_document_id'), 'document_tags', ['document_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_document_tags_document_id'), table_name='document_tags')
    op.drop_table('document_tags')
    op.drop_index(op.f('ix_document_tasks_task_id'), table_name='document_tasks')
    op.drop_index(op.f('ix_document_tasks_document_id'), table_name='document_tasks')
    op.drop_table('document_tasks')
    op.drop_column('leases', 'payment_status')
    op.drop_column('documents', 'confirmed_at')
    op.drop_column('messages', 'related_task_ids')
    op.drop_column('messages', 'approval_status')
    op.drop_column('messages', 'draft_reply')
    op.drop_column('messages', 'is_ai')
    op.drop_column('messages', 'sender_name')
    op.drop_column('messages', 'message_type')
    op.drop_column('conversations', 'last_message_at')
    op.drop_column('conversations', 'confidential')
    op.drop_column('conversations', 'priority')
    op.drop_column('conversations', 'urgency')
    op.drop_column('conversations', 'category')
    op.drop_column('conversations', 'source')
    op.drop_column('conversations', 'task_mode')
    op.drop_column('conversations', 'task_status')
    op.drop_column('conversations', 'is_task')
