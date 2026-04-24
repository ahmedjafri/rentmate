"""add id_sequences and retire task_number_sequences

Revision ID: 9a2c3e1b5d4f
Revises: 8f4b0d6b7c2a
Create Date: 2026-04-23 21:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9a2c3e1b5d4f"
down_revision: Union[str, Sequence[str], None] = "8f4b0d6b7c2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "id_sequences" not in existing:
        op.create_table(
            "id_sequences",
            sa.Column("org_id", sa.Integer(), nullable=False),
            sa.Column("entity_type", sa.String(length=64), nullable=False),
            sa.Column("last_number", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("org_id", "entity_type"),
        )

    # Seed id_sequences from the highest of: existing task_number_sequences.last_number,
    # MAX(tasks.id) per org, and MAX(suggestions.id) per org.
    op.execute(
        """
        INSERT INTO id_sequences (org_id, entity_type, last_number)
        SELECT org_id, entity_type, MAX(last_number)
        FROM (
            SELECT org_id, 'task'::varchar AS entity_type, last_number
              FROM task_number_sequences
            UNION ALL
            SELECT org_id, 'task'::varchar, COALESCE(MAX(id), 0)
              FROM tasks GROUP BY org_id
            UNION ALL
            SELECT org_id, 'suggestion'::varchar, COALESCE(MAX(id), 0)
              FROM suggestions GROUP BY org_id
        ) AS seed
        GROUP BY org_id, entity_type
        ON CONFLICT (org_id, entity_type) DO NOTHING
        """
    )

    if "task_number_sequences" in existing:
        op.drop_table("task_number_sequences")

    # Convert tasks / suggestions to composite PRIMARY KEY (org_id, id) so the same
    # numeric id can exist in multiple orgs. The existing single-column PK on `id`
    # and the redundant UniqueConstraint on (org_id, id) are both dropped.
    if bind.dialect.name == "postgresql":
        for table, old_uq in (
            ("tasks", "uq_tasks_server"),
            ("suggestions", "uq_suggestions_org"),
        ):
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {old_uq}")
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey")
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT pk_{table} PRIMARY KEY (org_id, id)"
            )
            op.execute(f"ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT")
            op.execute(f"DROP SEQUENCE IF EXISTS {table}_id_seq")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if bind.dialect.name == "postgresql":
        for table, old_uq in (
            ("tasks", "uq_tasks_server"),
            ("suggestions", "uq_suggestions_org"),
        ):
            op.execute(f"CREATE SEQUENCE IF NOT EXISTS {table}_id_seq")
            op.execute(
                f"SELECT setval('{table}_id_seq', COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
            )
            op.execute(
                f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{table}_id_seq')"
            )
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS pk_{table}")
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {table}_pkey PRIMARY KEY (id)"
            )
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {old_uq} UNIQUE (org_id, id)"
            )

    if "task_number_sequences" not in existing:
        op.create_table(
            "task_number_sequences",
            sa.Column("org_id", sa.Integer(), nullable=False),
            sa.Column("last_number", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("org_id"),
        )
        op.execute(
            """
            INSERT INTO task_number_sequences (org_id, last_number)
            SELECT org_id, last_number
              FROM id_sequences
             WHERE entity_type = 'task'
            """
        )

    if "id_sequences" in existing:
        op.drop_table("id_sequences")
