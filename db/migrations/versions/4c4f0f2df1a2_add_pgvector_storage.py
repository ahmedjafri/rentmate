"""add pgvector storage

Revision ID: 4c4f0f2df1a2
Revises: 13fb4c59240f
Create Date: 2026-04-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c4f0f2df1a2"
down_revision: Union[str, Sequence[str], None] = "13fb4c59240f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_item_vectors (
            memory_item_id TEXT PRIMARY KEY,
            org_id INTEGER NOT NULL,
            creator_id INTEGER NOT NULL,
            embedding vector(128) NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_memory_item_vectors_lookup
        ON memory_item_vectors (org_id, creator_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_memory_item_vectors_embedding
        ON memory_item_vectors
        USING ivfflat (embedding vector_cosine_ops)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_chunk_vectors (
            id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json JSONB,
            embedding vector(1536)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_chunk_vectors_doc_id
        ON document_chunk_vectors (doc_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_chunk_vectors_embedding
        ON document_chunk_vectors
        USING ivfflat (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP TABLE IF EXISTS document_chunk_vectors")
    op.execute("DROP TABLE IF EXISTS memory_item_vectors")
