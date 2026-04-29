"""add lease tenants association

Revision ID: c8d9e0f1a2b3
Revises: c2f3e4d5a6b8
Create Date: 2026-04-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
# Chained after the agent-memory backfill (which landed via main while
# this branch was open) so alembic sees a single head.
down_revision = "c2f3e4d5a6b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lease_tenants",
        sa.Column("lease_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "lease_id"],
            ["leases.org_id", "leases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["tenants.org_id", "tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("org_id", "lease_id", "tenant_id", name="pk_lease_tenants"),
    )
    op.create_index(op.f("ix_lease_tenants_org_id"), "lease_tenants", ["org_id"], unique=False)
    op.create_index(op.f("ix_lease_tenants_tenant_id"), "lease_tenants", ["tenant_id"], unique=False)
    op.execute(
        """
        INSERT INTO lease_tenants (org_id, lease_id, tenant_id, created_at)
        SELECT org_id, id, tenant_id, COALESCE(created_at, NOW())
        FROM leases
        WHERE tenant_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_lease_tenants_tenant_id"), table_name="lease_tenants")
    op.drop_index(op.f("ix_lease_tenants_org_id"), table_name="lease_tenants")
    op.drop_table("lease_tenants")
