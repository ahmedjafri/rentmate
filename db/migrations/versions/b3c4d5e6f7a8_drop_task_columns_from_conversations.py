"""Drop task columns from conversations table

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-03-28

Removes the task-specific columns that were migrated to the tasks table
in the previous revision.  Kept as a separate revision so rollback is
possible independently.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b3c4d5e6f7a8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None

_TASK_COLUMNS = [
    ("is_task",      sa.Boolean,    {"nullable": False, "server_default": "false"}),
    ("task_status",  sa.String(20), {"nullable": True}),
    ("task_mode",    sa.String(25), {"nullable": True}),
    ("source",       sa.String(25), {"nullable": True}),
    ("category",     sa.String(20), {"nullable": True}),
    ("urgency",      sa.String(20), {"nullable": True}),
    ("priority",     sa.String(20), {"nullable": True}),
    ("confidential", sa.Boolean,    {"nullable": False, "server_default": "false"}),
    ("last_message_at", sa.DateTime, {"nullable": True}),
    ("channel_type", sa.String(20), {"nullable": True}),
]


def upgrade():
    for col_name, *_ in _TASK_COLUMNS:
        op.drop_column("conversations", col_name)


def downgrade():
    for col_name, col_type, kwargs in _TASK_COLUMNS:
        op.add_column("conversations", sa.Column(col_name, col_type, **kwargs))
