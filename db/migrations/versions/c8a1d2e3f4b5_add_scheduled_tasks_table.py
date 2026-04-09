"""Add scheduled_tasks table

Revision ID: c8a1d2e3f4b5
Revises: b0f474129d70
Create Date: 2026-04-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8a1d2e3f4b5'
down_revision: Union[str, Sequence[str], None] = 'b0f474129d70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'scheduled_tasks',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('creator_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('schedule', sa.String(length=100), nullable=False),
        sa.Column('schedule_display', sa.String(length=255), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('state', sa.String(length=20), nullable=False, server_default='scheduled'),
        sa.Column('repeat', sa.Integer(), nullable=True),
        sa.Column('completed_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_run_at', sa.DateTime(), nullable=True),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('last_status', sa.String(length=20), nullable=True),
        sa.Column('last_output', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_scheduled_tasks_creator_id', 'scheduled_tasks', ['creator_id'])


def downgrade() -> None:
    op.drop_index('ix_scheduled_tasks_creator_id', 'scheduled_tasks')
    op.drop_table('scheduled_tasks')
