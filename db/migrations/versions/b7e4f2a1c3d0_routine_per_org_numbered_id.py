"""convert routines.id from UUID string to per-org integer with composite PK

Revision ID: b7e4f2a1c3d0
Revises: 9a2c3e1b5d4f
Create Date: 2026-04-23 22:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4f2a1c3d0"
down_revision: Union[str, Sequence[str], None] = "9a2c3e1b5d4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Stage a new integer column populated with a per-org row_number. Existing
    # UUID ids are discarded; no FKs point at routines.id so this is safe.
    op.execute("ALTER TABLE routines ADD COLUMN new_id INTEGER")
    op.execute(
        """
        UPDATE routines AS h
           SET new_id = sub.rn
          FROM (
              SELECT id,
                     row_number() OVER (PARTITION BY org_id ORDER BY created_at, id) AS rn
                FROM routines
          ) AS sub
         WHERE h.id = sub.id
        """
    )

    # Seed id_sequences so future allocator calls pick up after the max per org.
    op.execute(
        """
        INSERT INTO id_sequences (org_id, entity_type, last_number)
        SELECT org_id, 'routine', COALESCE(MAX(new_id), 0)
          FROM routines
         GROUP BY org_id
        ON CONFLICT (org_id, entity_type) DO UPDATE
           SET last_number = GREATEST(id_sequences.last_number, EXCLUDED.last_number)
        """
    )

    op.execute("ALTER TABLE routines DROP CONSTRAINT IF EXISTS uq_routines_org")
    op.execute("ALTER TABLE routines DROP CONSTRAINT IF EXISTS routines_pkey")
    op.execute("ALTER TABLE routines DROP COLUMN id")
    op.execute("ALTER TABLE routines RENAME COLUMN new_id TO id")
    op.execute("ALTER TABLE routines ALTER COLUMN id SET NOT NULL")
    op.execute(
        "ALTER TABLE routines ADD CONSTRAINT pk_routines PRIMARY KEY (org_id, id)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("ALTER TABLE routines ADD COLUMN new_id VARCHAR(36)")
    op.execute("UPDATE routines SET new_id = gen_random_uuid()::text")
    op.execute("ALTER TABLE routines DROP CONSTRAINT IF EXISTS pk_routines")
    op.execute("ALTER TABLE routines DROP COLUMN id")
    op.execute("ALTER TABLE routines RENAME COLUMN new_id TO id")
    op.execute("ALTER TABLE routines ALTER COLUMN id SET NOT NULL")
    op.execute("ALTER TABLE routines ADD CONSTRAINT routines_pkey PRIMARY KEY (id)")
    op.execute(
        "ALTER TABLE routines ADD CONSTRAINT uq_routines_org UNIQUE (org_id, id)"
    )
    op.execute(
        "DELETE FROM id_sequences WHERE entity_type = 'routine'"
    )
