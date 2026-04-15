from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Column, Index, Integer, String, Text
from sqlalchemy.orm import declarative_base

MEMORY_EMBED_DIM = 128
DOCUMENT_EMBED_DIM = 1536

VectorBase = declarative_base()


class MemoryItemVector(VectorBase):
    __tablename__ = "memory_item_vectors"

    memory_item_id = Column(String(64), primary_key=True)
    org_id = Column(Integer, nullable=False, index=True)
    creator_id = Column(Integer, nullable=False, index=True)
    embedding = Column(Vector(MEMORY_EMBED_DIM), nullable=False)

    __table_args__ = (
        Index(
            "ix_memory_item_vectors_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class DocumentChunkVector(VectorBase):
    __tablename__ = "document_chunk_vectors"

    id = Column(String(128), primary_key=True)
    doc_id = Column(String(64), nullable=False, index=True)
    content = Column(Text, nullable=False)
    metadata_json = Column(JSON, nullable=True)
    embedding = Column(Vector(DOCUMENT_EMBED_DIM), nullable=True)

    __table_args__ = (
        Index(
            "ix_document_chunk_vectors_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
