"""Add tasks table and link conversations

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-03-28

Creates the first-class tasks table and migrates existing is_task=TRUE
conversation rows into it.  Each old task conversation keeps its UUID;
a matching Task row is created with that same UUID, and task_id is set
to point back to it.

document_tasks.task_id FK is moved from conversations → tasks.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = 'a2b3c4d5e6f7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Create tasks table
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("task_status", sa.String(20), nullable=True),
        sa.Column("task_mode", sa.String(25), nullable=True),
        sa.Column("source", sa.String(25), nullable=True),
        sa.Column("category", sa.String(20), nullable=True),
        sa.Column("urgency", sa.String(20), nullable=True),
        sa.Column("priority", sa.String(20), nullable=True),
        sa.Column("confidential", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("last_message_at", sa.DateTime, nullable=True),
        sa.Column("channel_type", sa.String(20), nullable=True),
        sa.Column("property_id", sa.String(36), sa.ForeignKey("properties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("unit_id", sa.String(36), sa.ForeignKey("units.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lease_id", sa.String(36), sa.ForeignKey("leases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tasks_account_id", "tasks", ["account_id"])
    op.create_index("ix_tasks_updated_at", "tasks", ["updated_at"])

    # 2. Add task_id column to conversations
    op.add_column("conversations", sa.Column("task_id", sa.String(36), nullable=True))
    op.create_index("ix_conversations_task_id", "conversations", ["task_id"])

    # 3. Migrate existing task rows: INSERT into tasks using the conversation data
    if conn.dialect.name == "postgresql":
        conn.execute(text("""
            INSERT INTO tasks (
                id, account_id, title, task_status, task_mode, source, category,
                urgency, priority, confidential, last_message_at, channel_type,
                property_id, unit_id, lease_id, created_at, updated_at
            )
            SELECT
                id, account_id, subject, task_status, task_mode, source, category,
                urgency, priority, confidential, last_message_at, channel_type,
                property_id, unit_id, lease_id, created_at, updated_at
            FROM conversations
            WHERE is_task = TRUE
        """))

        # 4. Set task_id on those conversations to point at their new Task row
        conn.execute(text("""
            UPDATE conversations SET task_id = id WHERE is_task = TRUE
        """))

    # 5. Add FK constraint from conversations.task_id → tasks.id
    op.create_foreign_key(
        "fk_conversations_task_id_tasks",
        "conversations",
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 6. Move document_tasks.task_id FK from conversations → tasks
    #    First drop the old FK (find its name dynamically)
    insp = inspect(conn)
    for fk in insp.get_foreign_keys("document_tasks"):
        if fk.get("referred_table") == "conversations" and "task_id" in fk.get("constrained_columns", []):
            fk_name = fk.get("name")
            if fk_name:
                op.drop_constraint(fk_name, "document_tasks", type_="foreignkey")
            break

    op.create_foreign_key(
        "fk_document_tasks_task_id_tasks",
        "document_tasks",
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade():
    conn = op.get_bind()

    # Reverse FK on document_tasks
    op.drop_constraint("fk_document_tasks_task_id_tasks", "document_tasks", type_="foreignkey")
    op.create_foreign_key(
        "fk_document_tasks_task_id_conversations",
        "document_tasks",
        "conversations",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Remove task_id FK and column from conversations
    op.drop_constraint("fk_conversations_task_id_tasks", "conversations", type_="foreignkey")
    op.drop_index("ix_conversations_task_id", "conversations")
    op.drop_column("conversations", "task_id")

    # Drop tasks table
    op.drop_index("ix_tasks_updated_at", "tasks")
    op.drop_index("ix_tasks_account_id", "tasks")
    op.drop_table("tasks")
