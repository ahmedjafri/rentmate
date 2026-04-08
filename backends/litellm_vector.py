"""Lightweight vector backend using litellm embeddings + SQLite.

No torch/nvidia dependencies — uses the same LLM API for embeddings.
Stores vectors in a simple SQLite table with cosine similarity search.
"""
import json
import os
import uuid

import litellm
from sqlalchemy import Column, String, Text, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

_VectorBase = declarative_base()

_data_dir = os.getenv("RENTMATE_DATA_DIR", "./data")
_DB_PATH = os.path.join(_data_dir, "vectors.db")


class DocumentChunk(_VectorBase):
    __tablename__ = "document_chunks"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    doc_id = Column(String(36), nullable=False, index=True)
    content = Column(Text, nullable=False)
    metadata_json = Column(Text, nullable=True)
    embedding = Column(Text, nullable=True)  # JSON array of floats


class LiteLLMVectorBackend:
    """Vector store using litellm embeddings + SQLite."""

    def __init__(self):
        os.makedirs(os.path.dirname(_DB_PATH) or ".", exist_ok=True)
        self._engine = create_engine(f"sqlite:///{_DB_PATH}")
        _VectorBase.metadata.create_all(self._engine, checkfirst=True)
        self._Session = sessionmaker(bind=self._engine)

    def _get_embedding_config(self) -> tuple[str, str | None, str | None]:
        """Return (model, api_key, api_base) for the embedding call."""
        model = os.getenv("EMBEDDING_MODEL")
        api_key = os.getenv("LLM_API_KEY", "")
        api_base = os.getenv("LLM_BASE_URL") or None

        if model:
            return model, api_key or None, api_base

        # Infer from the LLM provider — use their embedding model
        llm_model = os.getenv("LLM_MODEL", "")
        provider = llm_model.split("/")[0] if "/" in llm_model else ""

        _PROVIDER_EMBEDDING = {
            "openai": "text-embedding-3-small",
            "anthropic": "text-embedding-3-small",  # Anthropic has no embedding API; fall back to OpenAI
            "deepseek": "deepseek/text-embedding-v1",  # hypothetical; DeepSeek may not have embeddings
        }

        if provider in _PROVIDER_EMBEDDING:
            return _PROVIDER_EMBEDDING[provider], api_key or None, api_base
        return "text-embedding-3-small", api_key or None, api_base

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Get embeddings via litellm. Returns empty lists if embedding fails."""
        model, api_key, api_base = self._get_embedding_config()
        try:
            kwargs: dict = {"model": model, "input": texts}
            if api_key:
                kwargs["api_key"] = api_key
            if api_base:
                kwargs["api_base"] = api_base
            resp = litellm.embedding(**kwargs)
            return [d["embedding"] for d in resp.data]
        except Exception as e:
            print(f"[vector] Embedding failed ({model}): {e}")
            return [[] for _ in texts]

    def add_document(self, doc_id: str, *, chunks: list[str], metadatas: list[dict]) -> None:
        if not chunks:
            return
        embeddings = self._embed(chunks)
        db = self._Session()
        try:
            # Delete existing chunks for this doc
            db.execute(text("DELETE FROM document_chunks WHERE doc_id = :did"), {"did": doc_id})
            for i, (chunk, meta, emb) in enumerate(zip(chunks, metadatas, embeddings)):
                db.add(DocumentChunk(
                    id=f"{doc_id}::{i}",
                    doc_id=doc_id,
                    content=chunk,
                    metadata_json=json.dumps(meta),
                    embedding=json.dumps(emb) if emb else None,
                ))
            db.commit()
        finally:
            db.close()

    def query(self, text_query: str, *, n_results: int = 5, filter: dict | None = None) -> list[dict]:
        """Simple cosine similarity search."""
        query_emb = self._embed([text_query])
        if not query_emb or not query_emb[0]:
            return []
        q_vec = query_emb[0]

        db = self._Session()
        try:
            chunks = db.query(DocumentChunk).all()
            scored = []
            for chunk in chunks:
                if not chunk.embedding:
                    continue
                if filter:
                    meta = json.loads(chunk.metadata_json or "{}")
                    if not all(meta.get(k) == v for k, v in filter.items()):
                        continue
                c_vec = json.loads(chunk.embedding)
                score = _cosine_sim(q_vec, c_vec)
                scored.append((score, chunk))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [
                {"content": c.content, "metadata": json.loads(c.metadata_json or "{}")}
                for _, c in scored[:n_results]
            ]
        finally:
            db.close()

    def delete_document(self, doc_id: str) -> None:
        db = self._Session()
        try:
            db.execute(text("DELETE FROM document_chunks WHERE doc_id = :did"), {"did": doc_id})
            db.commit()
        finally:
            db.close()


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
