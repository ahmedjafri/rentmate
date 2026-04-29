"""Backfill agent_memory.agent_id on legacy schemas

The ``agent_memory.agent_id`` column has been declared on the model
since the baseline schema, but production hosted databases that were
provisioned before the alembic baseline existed got stamped at
``13fb4c59240f`` (head at the time) without the baseline migration
ever running. The column has therefore been silently missing on those
DBs ever since, and the most recent rentmate bump tripped on it
(``UndefinedColumn: column agent_memory.agent_id does not exist``)
which then aborted every downstream query in the same transaction.

This migration adds the column ``IF NOT EXISTS`` and backfills any
existing rows from ``creator_id`` (the lambda default the model uses
for new inserts), then locks down ``NOT NULL`` + ``DEFAULT ''``.
Idempotent — a clean baseline install that already created the column
makes this a no-op.

Revision ID: c2f3e4d5a6b8
Revises: b4d8e9f0a1c2
Create Date: 2026-04-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c2f3e4d5a6b8"
down_revision: Union[str, Sequence[str], None] = "b4d8e9f0a1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agent_memory" not in inspector.get_table_names():
        # Fresh install where the table doesn't exist yet — let the
        # baseline migration handle it.
        return
    cols = {c["name"] for c in inspector.get_columns("agent_memory")}
    if "agent_id" in cols:
        return

    op.add_column(
        "agent_memory",
        sa.Column("agent_id", sa.String(length=255), nullable=True),
    )
    op.execute(
        "UPDATE agent_memory SET agent_id = creator_id::text "
        "WHERE agent_id IS NULL"
    )
    op.alter_column("agent_memory", "agent_id", nullable=False, server_default="")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agent_memory" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("agent_memory")}
    if "agent_id" not in cols:
        return
    op.drop_column("agent_memory", "agent_id")
