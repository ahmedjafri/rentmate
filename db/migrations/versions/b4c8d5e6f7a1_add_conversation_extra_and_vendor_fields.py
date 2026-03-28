"""add conversation.extra and external_contacts.account_id

Revision ID: b4c8d5e6f7a1
Revises: a1b2c3d4e5f6
Create Date: 2026-03-28

"""
from alembic import op
import sqlalchemy as sa

revision = 'b4c8d5e6f7a1'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversations', sa.Column('extra', sa.JSON, nullable=True))
    op.add_column('external_contacts', sa.Column('account_id', sa.String(36), nullable=True))


def downgrade():
    op.drop_column('conversations', 'extra')
    op.drop_column('external_contacts', 'account_id')
