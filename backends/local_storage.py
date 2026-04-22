import os
import tempfile
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


def _required_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value) if value else None


def _write_probe(path: Path) -> None:
    with tempfile.NamedTemporaryFile(dir=path, prefix=".rentmate-write-test-", delete=True):
        pass


def ensure_runtime_storage_contract() -> tuple[Path, Path]:
    data_dir = _env_path("RENTMATE_DATA_DIR", "./data")
    docs_dir = _env_path("RENTMATE_DOCS_DIR", f"{data_dir}/documents")
    required_data_dir = _required_path("RENTMATE_REQUIRED_DATA_DIR")
    required_docs_dir = _required_path("RENTMATE_REQUIRED_DOCS_DIR")

    if required_data_dir is None and required_docs_dir is None:
        data_dir.mkdir(parents=True, exist_ok=True)
        docs_dir.mkdir(parents=True, exist_ok=True)
        return data_dir, docs_dir

    if required_data_dir is not None and data_dir != required_data_dir:
        raise RuntimeError(
            f"RENTMATE_DATA_DIR must be set to {required_data_dir} for this runtime."
        )
    if required_docs_dir is not None and docs_dir != required_docs_dir:
        raise RuntimeError(
            f"RENTMATE_DOCS_DIR must be set to {required_docs_dir} for this runtime."
        )
    if not data_dir.exists() or not data_dir.is_dir():
        raise RuntimeError(
            f"Runtime storage path is missing or not a directory: {data_dir}."
        )

    _write_probe(data_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)
    _write_probe(docs_dir)
    return data_dir, docs_dir


class LocalStorageBackend:
    def __init__(self):
        _, self.docs_dir = ensure_runtime_storage_contract()

    async def upload(self, path: str, *, data: bytes, content_type: str) -> str:
        dest = self.docs_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return path

    async def download(self, path: str) -> bytes:
        dest = self.docs_dir / path
        if not dest.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return dest.read_bytes()

    async def delete(self, path: str) -> None:
        dest = self.docs_dir / path
        if dest.exists():
            dest.unlink()
        # Remove empty parent dir (the doc-id folder)
        try:
            dest.parent.rmdir()
        except OSError:
            pass
