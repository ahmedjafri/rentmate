"""Add tenant_id to units for direct tenant-unit association

Revision ID: 4497a7711a90
Revises: 3e66152ff9a1
Create Date: 2026-04-08 20:17:24.497469

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4497a7711a90'
down_revision: Union[str, Sequence[str], None] = '3e66152ff9a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('units') as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key('fk_units_tenant_id', 'tenants', ['tenant_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('units') as batch_op:
        batch_op.drop_constraint('fk_units_tenant_id', type_='foreignkey')
        batch_op.drop_column('tenant_id')
