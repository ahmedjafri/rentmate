from abc import ABC, abstractmethod


class AuthBackend(ABC):
    @abstractmethod
    async def validate_token(self, token: str) -> dict:
        """Returns {uid, email, username}"""

    @abstractmethod
    async def login(self, **credentials) -> str:
        """Returns JWT access_token"""


class StorageBackend(ABC):
    @abstractmethod
    async def upload(self, path: str, *, data: bytes, content_type: str) -> str:
        """Returns storage path"""

    @abstractmethod
    async def download(self, path: str) -> bytes: ...

    async def delete(self, path: str) -> None:
        """Best-effort delete — subclasses may override."""


class VectorBackend(ABC):
    @abstractmethod
    def add_document(self, doc_id: str, *, chunks: list[str], metadatas: list[dict]) -> None: ...

    @abstractmethod
    def query(self, text: str, *, n_results: int = 5, filter: dict | None = None) -> list[dict]: ...

    @abstractmethod
    def delete_document(self, doc_id: str) -> None: ...


class SMSRouter(ABC):
    @abstractmethod
    def resolve(self, db, *, from_number: str, to_number: str) -> tuple | None:
        """
        Returns (creator_id, entity, direction, entity_type) or None.
        """
