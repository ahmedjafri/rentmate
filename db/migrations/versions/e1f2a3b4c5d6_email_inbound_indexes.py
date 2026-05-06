"""Add JSONB expression indexes for email inbound ingestion.

These indexes make two hot paths fast:

1. Thread lookup — when a reply arrives, find the existing MIRRORED_CHAT
   conversation by its email_thread_id stored in Conversation.extra.
2. Deduplication — check whether we already stored a message by its RFC
   Message-ID stored in Message.meta before inserting a duplicate.

This migration extends the e5a1b3c7d9f2 head (add_property_owner_id).
The a1b2c3d4e5f6 head (add_task_last_seen_at) is a pre-existing separate
branch in this repo and is intentionally left as a parallel branch head.

SQLAlchemy's create_all cannot generate expression indexes on JSON fields
so they live here as raw SQL.  The downgrade() mirrors the upgrade() in
reverse so rollbacks are clean.

Revision ID: e1f2a3b4c5d6
Revises: a1b2c3d4e5f6, e5a1b3c7d9f2
Create Date: 2026-05-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e1f2a3b4c5d6"
# Points to the add_property_owner_id head.  The other existing branch tip
# (a1b2c3d4e5f6 / add_task_last_seen_at) is a pre-existing separate branch
# in this repo and is intentionally left as-is.
down_revision: Union[str, Sequence[str], None] = "e5a1b3c7d9f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fast thread-keyed lookup: "does a MIRRORED_CHAT conversation already
    # exist for this email thread?"  Partial index only covers the relevant
    # conversation type so it stays small even as the table grows.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_conversations_email_thread_id
            ON conversations ((extra->>'email_thread_id'))
            WHERE conversation_type = 'mirrored_chat'
    """)

    # Uniqueness guard: prevents two concurrent inbound emails starting the
    # same thread from each creating their own conversation row.  The partial
    # constraint only applies when the field is set so non-email mirrored
    # conversations (e.g. from the browser extension) are unaffected.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_email_thread_id
            ON conversations ((extra->>'email_thread_id'))
            WHERE conversation_type = 'mirrored_chat'
              AND extra->>'email_thread_id' IS NOT NULL
    """)

    # Fast deduplication: "did we already store this RFC Message-ID?"
    # Partial index so it only covers messages that actually have the field.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_messages_email_message_id
            ON messages ((meta->>'email_message_id'))
            WHERE meta->>'email_message_id' IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_email_message_id")
    op.execute("DROP INDEX IF EXISTS uq_conversations_email_thread_id")
    op.execute("DROP INDEX IF EXISTS ix_conversations_email_thread_id")
