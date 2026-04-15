from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

MEMORY_EMBED_DIM = 128
DOCUMENT_EMBED_DIM = 1536

VectorBase = declarative_base()


class MemoryItemVector(VectorBase):
    __tablename__ = "memory_item_vectors"

    memory_item_id = Column(Text, primary_key=True)
    org_id = Column(Integer, nullable=False)
    creator_id = Column(Integer, nullable=False)
    embedding = Column(Vector(MEMORY_EMBED_DIM), nullable=False)

    __table_args__ = (
        Index("ix_memory_item_vectors_lookup", "org_id", "creator_id"),
        Index(
            "ix_memory_item_vectors_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class DocumentChunkVector(VectorBase):
    __tablename__ = "document_chunk_vectors"

    id = Column(Text, primary_key=True)
    doc_id = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    metadata_json = Column(JSONB, nullable=True)
    embedding = Column(Vector(DOCUMENT_EMBED_DIM), nullable=True)

    __table_args__ = (
        Index("ix_document_chunk_vectors_doc_id", "doc_id"),
        Index(
            "ix_document_chunk_vectors_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
