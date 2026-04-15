"""Document vector backend backed by Postgres pgvector."""

import math
import os
import sys
from contextlib import nullcontext

import litellm
from sqlalchemy import Connection, create_engine, delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from db.vector_models import DOCUMENT_EMBED_DIM, DocumentChunkVector, VectorBase

_DB_URI = os.getenv("RENTMATE_DB_URI", "").strip()


def _is_pytest_runtime() -> bool:
    return (
        "pytest" in sys.modules
        or bool(os.getenv("PYTEST_CURRENT_TEST"))
        or bool(os.getenv("PYTEST_XDIST_WORKER"))
    )


def _normalize_embedding(embedding: list[float], dim: int) -> list[float]:
    if not embedding:
        return []
    if len(embedding) > dim:
        embedding = embedding[:dim]
    elif len(embedding) < dim:
        embedding = embedding + [0.0] * (dim - len(embedding))
    norm = math.sqrt(sum(v * v for v in embedding)) or 1.0
    return [v / norm for v in embedding]


class LiteLLMVectorBackend:
    """Vector store using litellm embeddings and Postgres pgvector."""

    def __init__(self):
        self._bind = create_engine(_DB_URI) if _DB_URI else None
        self._dialect = self._bind.dialect.name if self._bind is not None else ""
        self._enabled = self._dialect == "postgresql"
        if self._enabled:
            self._ensure_schema()

    def _connection_scope(self):
        if isinstance(self._bind, Connection):
            return nullcontext(self._bind)
        return self._bind.begin()

    def _ensure_schema(self) -> None:
        try:
            with self._connection_scope() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                VectorBase.metadata.create_all(bind=conn, tables=[DocumentChunkVector.__table__], checkfirst=True)
        except SQLAlchemyError as exc:
            if _is_pytest_runtime():
                print(f"[vector] pgvector unavailable in test DB, disabling document vectors: {exc}")
                self._enabled = False
                return
            raise

    def _get_embedding_config(self) -> tuple[str, str | None, str | None]:
        model = os.getenv("EMBEDDING_MODEL")
        api_key = os.getenv("LLM_API_KEY", "")
        api_base = os.getenv("LLM_BASE_URL") or None

        if model:
            return model, api_key or None, api_base

        llm_model = os.getenv("LLM_MODEL", "")
        provider = llm_model.split("/")[0] if "/" in llm_model else ""
        provider_embedding = {
            "openai": "text-embedding-3-small",
            "anthropic": "text-embedding-3-small",
            "deepseek": "deepseek/text-embedding-v1",
        }
        if provider in provider_embedding:
            return provider_embedding[provider], api_key or None, api_base
        return "text-embedding-3-small", api_key or None, api_base

    def _embed(self, texts: list[str]) -> list[list[float]]:
        model, api_key, api_base = self._get_embedding_config()
        try:
            kwargs: dict = {"model": model, "input": texts}
            if api_key:
                kwargs["api_key"] = api_key
            if api_base:
                kwargs["api_base"] = api_base
            resp = litellm.embedding(**kwargs)
            return [_normalize_embedding(d["embedding"], DOCUMENT_EMBED_DIM) for d in resp.data]
        except Exception as exc:
            print(f"[vector] Embedding failed ({model}): {exc}")
            return [[] for _ in texts]

    def add_document(self, doc_id: str, *, chunks: list[str], metadatas: list[dict]) -> None:
        if not self._enabled or not chunks:
            return

        embeddings = self._embed(chunks)
        rows = [
            {
                "id": f"{doc_id}::{i}",
                "doc_id": doc_id,
                "content": chunk,
                "metadata_json": meta,
                "embedding": emb or None,
            }
            for i, (chunk, meta, emb) in enumerate(zip(chunks, metadatas, embeddings))
        ]

        with self._connection_scope() as conn:
            conn.execute(delete(DocumentChunkVector).where(DocumentChunkVector.doc_id == doc_id))
            if rows:
                stmt = insert(DocumentChunkVector).values(rows)
                conn.execute(stmt)

    def query(self, text_query: str, *, n_results: int = 5, filter: dict | None = None) -> list[dict]:
        if not self._enabled:
            return []

        query_emb = self._embed([text_query])
        if not query_emb or not query_emb[0]:
            return []

        distance = DocumentChunkVector.embedding.cosine_distance(query_emb[0])
        stmt = (
            select(DocumentChunkVector.content, DocumentChunkVector.metadata_json)
            .where(DocumentChunkVector.embedding.is_not(None))
            .order_by(distance)
            .limit(n_results)
        )
        if filter:
            for key, value in filter.items():
                stmt = stmt.where(DocumentChunkVector.metadata_json[key].astext == str(value))

        with self._connection_scope() as conn:
            rows = conn.execute(stmt).all()
        return [{"content": row.content, "metadata": row.metadata_json or {}} for row in rows]

    def delete_document(self, doc_id: str) -> None:
        if not self._enabled:
            return
        with self._connection_scope() as conn:
            conn.execute(delete(DocumentChunkVector).where(DocumentChunkVector.doc_id == doc_id))
