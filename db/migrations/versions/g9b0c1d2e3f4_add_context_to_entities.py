"""Add context column to entity tables

Revision ID: g9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-04-06

Agent-managed context notes stored directly on entity tables
(properties, units, tenants, external_contacts).
"""
from alembic import op
import sqlalchemy as sa

revision = 'g9b0c1d2e3f4'
down_revision = 'f8a9b0c1d2e3'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('properties', 'units', 'tenants', 'external_contacts'):
        op.add_column(table, sa.Column('context', sa.Text(), nullable=True))


def downgrade():
    for table in ('properties', 'units', 'tenants', 'external_contacts'):
        op.drop_column(table, 'context')
