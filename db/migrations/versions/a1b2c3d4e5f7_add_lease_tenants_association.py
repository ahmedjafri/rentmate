"""Add lease_tenants many-to-many association table

Revision ID: a1b2c3d4e5f7
Revises: e7f8a9b0c1d2
Create Date: 2026-04-06

Supports multiple tenants per lease (roommates). The new lease_tenants
join table links leases to tenants in a many-to-many relationship.
The existing leases.tenant_id FK is kept for backward compatibility.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f7'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'lease_tenants',
        sa.Column('lease_id', sa.String(36), sa.ForeignKey('leases.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
    )
    # Back-fill: every existing lease's primary tenant should appear in the join table
    op.execute(
        "INSERT INTO lease_tenants (lease_id, tenant_id) "
        "SELECT id, tenant_id FROM leases WHERE tenant_id IS NOT NULL"
    )


def downgrade():
    op.drop_table('lease_tenants')
