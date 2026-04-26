"""add notifications table

Revision ID: f6b7c8d9e0f1
Revises: f4a6b2c8d0e1
Create Date: 2026-04-24 20:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f6b7c8d9e0f1"
down_revision = "f4a6b2c8d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("recipient_user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("delivery_status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_id", sa.String(length=36), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "conversation_id"],
            ["conversations.org_id", "conversations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "recipient_user_id"],
            ["users.org_id", "users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "task_id"],
            ["tasks.org_id", "tasks.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
        sa.UniqueConstraint("org_id", "id", name="uq_notifications_org"),
    )
    op.create_index(op.f("ix_notifications_recipient_user_id"), "notifications", ["recipient_user_id"], unique=False)
    op.create_index(op.f("ix_notifications_task_id"), "notifications", ["task_id"], unique=False)
    op.create_index(op.f("ix_notifications_conversation_id"), "notifications", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_notifications_created_at"), "notifications", ["created_at"], unique=False)
    op.create_index(op.f("ix_notifications_org_id"), "notifications", ["org_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_org_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_created_at"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_conversation_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_task_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_recipient_user_id"), table_name="notifications")
    op.drop_table("notifications")
