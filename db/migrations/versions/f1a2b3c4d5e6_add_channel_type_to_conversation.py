"""add channel_type to conversation

Revision ID: f1a2b3c4d5e6
Revises: d5e8f3a2b1c4
Create Date: 2026-03-14

"""
from alembic import op
import sqlalchemy as sa

revision = 'f1a2b3c4d5e6'
down_revision = 'd5e8f3a2b1c4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversations', sa.Column('channel_type', sa.String(20), nullable=True))


def downgrade():
    op.drop_column('conversations', 'channel_type')
