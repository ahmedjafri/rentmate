"""rename scheduled_tasks to routines

Revision ID: 8f4b0d6b7c2a
Revises: 4c4f0f2df1a2
Create Date: 2026-04-23 20:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f4b0d6b7c2a"
down_revision: Union[str, Sequence[str], None] = "4c4f0f2df1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "scheduled_tasks" not in inspector.get_table_names():
        return

    op.rename_table("scheduled_tasks", "routines")

    if bind.dialect.name == "postgresql":
        op.execute("ALTER INDEX IF EXISTS ix_scheduled_tasks_org_id RENAME TO ix_routines_org_id")
        op.execute("ALTER TABLE routines RENAME CONSTRAINT uq_scheduled_tasks_org TO uq_routines_org")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "routines" not in inspector.get_table_names():
        return

    op.rename_table("routines", "scheduled_tasks")

    if bind.dialect.name == "postgresql":
        op.execute("ALTER INDEX IF EXISTS ix_routines_org_id RENAME TO ix_scheduled_tasks_org_id")
        op.execute("ALTER TABLE scheduled_tasks RENAME CONSTRAINT uq_routines_org TO uq_scheduled_tasks_org")
