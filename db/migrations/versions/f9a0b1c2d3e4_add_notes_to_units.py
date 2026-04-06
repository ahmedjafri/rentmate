"""Add notes column to units table

Revision ID: f9a0b1c2d3e4
Revises: e7f8a9b0c1d2
Create Date: 2026-04-06

Allows property managers to store free-text notes per unit (e.g. condition,
special instructions for the AI agent, appliance details).
"""
from alembic import op
import sqlalchemy as sa

revision = 'f9a0b1c2d3e4'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('units', sa.Column('notes', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('units', 'notes')
