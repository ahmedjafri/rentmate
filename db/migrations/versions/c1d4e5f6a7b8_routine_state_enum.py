"""convert routines.state from VARCHAR to routine_state_enum

Revision ID: c1d4e5f6a7b8
Revises: b7e4f2a1c3d0
Create Date: 2026-04-23 23:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c1d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b7e4f2a1c3d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        "CREATE TYPE routine_state_enum AS ENUM ('SCHEDULED', 'PAUSED', 'COMPLETED')"
    )
    op.execute("ALTER TABLE routines ALTER COLUMN state DROP DEFAULT")
    op.execute(
        "ALTER TABLE routines ALTER COLUMN state TYPE routine_state_enum "
        "USING UPPER(state)::routine_state_enum"
    )
    op.execute(
        "ALTER TABLE routines ALTER COLUMN state SET DEFAULT 'SCHEDULED'::routine_state_enum"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("ALTER TABLE routines ALTER COLUMN state DROP DEFAULT")
    op.execute(
        "ALTER TABLE routines ALTER COLUMN state TYPE VARCHAR(20) "
        "USING LOWER(state::text)"
    )
    op.execute("ALTER TABLE routines ALTER COLUMN state SET DEFAULT 'scheduled'")
    op.execute("DROP TYPE routine_state_enum")
