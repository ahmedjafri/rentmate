import os
from pathlib import Path

_data_dir = os.getenv("RENTMATE_DATA_DIR", "./data")
DOCS_DIR = Path(os.getenv("RENTMATE_DOCS_DIR", f"{_data_dir}/documents"))


class LocalStorageBackend:
    def __init__(self):
        DOCS_DIR.mkdir(parents=True, exist_ok=True)

    async def upload(self, path: str, data: bytes, content_type: str) -> str:
        dest = DOCS_DIR / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return path

    async def download(self, path: str) -> bytes:
        dest = DOCS_DIR / path
        if not dest.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return dest.read_bytes()

    async def delete(self, path: str) -> None:
        dest = DOCS_DIR / path
        if dest.exists():
            dest.unlink()
        # Remove empty parent dir (the doc-id folder)
        try:
            dest.parent.rmdir()
        except OSError:
            pass
