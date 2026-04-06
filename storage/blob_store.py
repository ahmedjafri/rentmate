"""BlobStore abstraction for agent workspace files.

Phase 1 of the nanobot-storage convergence (issue #2).  Introduces a
``BlobStore`` protocol that decouples callers from *where* bytes live.
``LocalBlobStore`` is the first (and currently only) implementation — it
reads/writes the local filesystem exactly as the code did before this
abstraction existed.

Future phases will add a ``DbBlobStore`` that stores content in the
``agent_files`` table, and eventually retire the local variant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class BlobStore(Protocol):
    """Minimal interface for reading and writing workspace blobs."""

    def read(self, path: str) -> str | None:
        """Return file content as a string, or ``None`` if the blob doesn't exist."""
        ...

    def write(self, path: str, content: str) -> None:
        """Persist *content* at *path*, creating intermediate dirs as needed."""
        ...

    def exists(self, path: str) -> bool:
        """Return whether a blob exists at *path*."""
        ...


class LocalBlobStore:
    """Blob store backed by the local filesystem.

    *root* is the base directory; all *path* arguments passed to
    ``read``/``write``/``exists`` are resolved relative to it.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    # -- BlobStore interface ---------------------------------------------------

    def read(self, path: str) -> str | None:
        target = self._root / path
        if not target.is_file():
            return None
        return target.read_text()

    def write(self, path: str, content: str) -> None:
        target = self._root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def exists(self, path: str) -> bool:
        return (self._root / path).is_file()
