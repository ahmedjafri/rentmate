"""Add notes column to tasks

Revision ID: i1d2e3f4g5h6
Revises: h0c1d2e3f4g5
Create Date: 2026-04-07

Task-scoped notes for agent observations (quotes, findings, scheduling).
Separate from entity context which is permanent knowledge.
"""
from alembic import op
import sqlalchemy as sa

revision = 'i1d2e3f4g5h6'
down_revision = 'h0c1d2e3f4g5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('notes', sa.String(), nullable=True))


def downgrade():
    op.drop_column('tasks', 'notes')
