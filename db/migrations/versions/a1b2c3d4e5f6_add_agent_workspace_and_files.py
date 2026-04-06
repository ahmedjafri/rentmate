"""add agent_workspaces and agent_files tables

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-04-06

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'agent_workspaces',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('account_id', sa.String(36), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )

    op.create_table(
        'agent_files',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('workspace_id', sa.String(36),
                  sa.ForeignKey('agent_workspaces.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('path', sa.String(512), nullable=False),
        sa.Column('content', sa.Text(), nullable=False, server_default=''),
        sa.Column('size_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )

    op.create_index(
        'ix_agent_files_workspace_path',
        'agent_files',
        ['workspace_id', 'path'],
        unique=True,
    )


def downgrade():
    op.drop_index('ix_agent_files_workspace_path', table_name='agent_files')
    op.drop_table('agent_files')
    op.drop_table('agent_workspaces')
