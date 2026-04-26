"""add task last_seen_at

Revision ID: a1b2c3d4e5f6
Revises: f6b7c8d9e0f1
Create Date: 2026-04-24 23:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.execute(
        """
        UPDATE tasks
        SET last_seen_at = COALESCE(last_reviewed_at, last_message_at, updated_at, created_at)
        WHERE last_seen_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("tasks", "last_seen_at")
