"""Add agent_traces table

Revision ID: h0c1d2e3f4g5
Revises: g9b0c1d2e3f4
Create Date: 2026-04-06

Persistent trace log for agent tool calls, LLM responses,
suggestion creation/execution, and errors.
"""
from alembic import op
import sqlalchemy as sa

revision = 'h0c1d2e3f4g5'
down_revision = 'g9b0c1d2e3f4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'agent_traces',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False, index=True),
        sa.Column('trace_type', sa.String(30), nullable=False, index=True),
        sa.Column('source', sa.String(50), nullable=True),
        sa.Column('task_id', sa.String(36), nullable=True, index=True),
        sa.Column('conversation_id', sa.String(36), nullable=True),
        sa.Column('tool_name', sa.String(50), nullable=True),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('suggestion_id', sa.String(36), nullable=True),
    )


def downgrade():
    op.drop_table('agent_traces')
