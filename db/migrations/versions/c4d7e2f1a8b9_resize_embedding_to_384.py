"""resize embedding column to 384 dims (local sentence-transformers)

Revision ID: c4d7e2f1a8b9
Revises: b3f9a1c2d4e5
Create Date: 2026-03-07 00:01:00.000000

"""
from typing import Sequence, Union
from alembic import op

revision: str = 'c4d7e2f1a8b9'
down_revision: Union[str, Sequence[str], None] = 'b3f9a1c2d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the IVFFlat index and old column, recreate at 384 dims
    op.execute("DROP INDEX IF EXISTS document_chunks_embedding_idx")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE document_chunks ADD COLUMN embedding vector(384)")
    op.execute(
        "CREATE INDEX ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS document_chunks_embedding_idx")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE document_chunks ADD COLUMN embedding vector(1536)")
