import os

_data_dir = os.getenv("RENTMATE_DATA_DIR", "./data")
CHROMA_PATH = os.getenv("RENTMATE_CHROMA_DIR", f"{_data_dir}/chroma")


class ChromaVectorBackend:
    def __init__(self):
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        self._client = chromadb.PersistentClient(path=CHROMA_PATH)
        self._ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        self._collection = self._client.get_or_create_collection(
            name="documents",
            embedding_function=self._ef,
        )

    def add_document(self, doc_id: str, *, chunks: list[str], metadatas: list[dict]) -> None:
        if not chunks:
            return
        ids = [f"{doc_id}::{i}" for i in range(len(chunks))]
        self._collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)

    def query(self, text: str, *, n_results: int = 5, filter: dict | None = None) -> list[dict]:
        kwargs: dict = {"query_texts": [text], "n_results": n_results}
        if filter:
            kwargs["where"] = filter
        results = self._collection.query(**kwargs)
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        return [{"content": d, "metadata": m} for d, m in zip(docs, metas)]

    def delete_document(self, doc_id: str) -> None:
        existing = self._collection.get(where={"doc_id": doc_id})
        if existing["ids"]:
            self._collection.delete(ids=existing["ids"])
