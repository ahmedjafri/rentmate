"""add property.owner_id

Revision ID: e5a1b3c7d9f2
Revises: d3f5a6b7c8d9
Create Date: 2026-05-05 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5a1b3c7d9f2"
down_revision: Union[str, Sequence[str], None] = "d3f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.add_column(
        "properties",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_properties_owner_id",
        "properties",
        ["owner_id"],
    )
    op.create_foreign_key(
        "fk_properties_owner_id",
        source_table="properties",
        referent_table="users",
        local_cols=["org_id", "owner_id"],
        remote_cols=["org_id", "id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        "ALTER TABLE properties DROP CONSTRAINT IF EXISTS fk_properties_owner_id"
    )
    op.drop_index("ix_properties_owner_id", table_name="properties")
    op.drop_column("properties", "owner_id")
