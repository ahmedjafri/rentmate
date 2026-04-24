"""move external_conversation_id from tasks to conversations.parent_task_id

Revision ID: d2e3f4a5b6c7
Revises: c1d4e5f6a7b8
Create Date: 2026-04-23 23:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.add_column(
        "conversations",
        sa.Column("parent_task_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_conversations_parent_task_id",
        "conversations",
        ["parent_task_id"],
    )

    # Migrate data: for each task with external_conversation_id set, copy onto
    # the linked conversation's parent_task_id.
    op.execute(
        """
        UPDATE conversations AS c
           SET parent_task_id = t.id
          FROM tasks AS t
         WHERE t.external_conversation_id = c.id
           AND t.org_id = c.org_id
        """
    )

    op.create_foreign_key(
        "fk_conversations_parent_task_id",
        source_table="conversations",
        referent_table="tasks",
        local_cols=["org_id", "parent_task_id"],
        remote_cols=["org_id", "id"],
        ondelete="SET NULL",
    )

    # Drop the old column + its FK from tasks.
    op.execute(
        "ALTER TABLE tasks DROP CONSTRAINT IF EXISTS "
        "tasks_org_id_external_conversation_id_fkey"
    )
    op.drop_column("tasks", "external_conversation_id")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.add_column(
        "tasks",
        sa.Column("external_conversation_id", sa.Integer(), nullable=True),
    )
    # Restore a single external_conversation_id per task by picking the most
    # recent linked conversation.
    op.execute(
        """
        UPDATE tasks AS t
           SET external_conversation_id = sub.id
          FROM (
              SELECT DISTINCT ON (parent_task_id, org_id)
                     parent_task_id, org_id, id
                FROM conversations
               WHERE parent_task_id IS NOT NULL
               ORDER BY parent_task_id, org_id, updated_at DESC
          ) AS sub
         WHERE sub.parent_task_id = t.id
           AND sub.org_id = t.org_id
        """
    )
    op.create_foreign_key(
        "tasks_org_id_external_conversation_id_fkey",
        source_table="tasks",
        referent_table="conversations",
        local_cols=["org_id", "external_conversation_id"],
        remote_cols=["org_id", "id"],
        ondelete="SET NULL",
    )

    op.execute(
        "ALTER TABLE conversations DROP CONSTRAINT IF EXISTS "
        "fk_conversations_parent_task_id"
    )
    op.drop_index("ix_conversations_parent_task_id", table_name="conversations")
    op.drop_column("conversations", "parent_task_id")
