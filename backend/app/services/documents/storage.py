"""Where an uploaded document's raw bytes live, independent of where its
metadata (documents table) or derived content (document_chunks) live.

Local filesystem storage is the only implementation for this demo - see
docs/RAG_INGESTION.md, "Storage architecture". `FileStorage` exists so a
later phase can swap in object storage (S3-compatible, etc.) without
touching the ingestion pipeline or any API route: they only ever call
`save`/`read`/`delete` on whatever `get_file_storage()` returns.
"""

import uuid
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.core.config import get_settings


class FileStorage(Protocol):
    def save(self, document_id: uuid.UUID, extension: str, content: bytes) -> str:
        """Persists `content`, returns an opaque storage key (not a
        filesystem path a caller should construct itself)."""
        ...

    def read(self, storage_key: str) -> bytes: ...

    def delete(self, storage_key: str) -> None: ...


def _safe_extension(extension: str) -> str:
    """Never trust a client-supplied filename's extension directly as part
    of a filesystem path - normalize to a short alphanumeric suffix only,
    so nothing resembling `../../etc/passwd` or a null byte ever reaches
    `Path`. The stored filename is otherwise entirely server-generated
    (the document's own id), so this is the only externally-influenced
    fragment of the path at all.
    """
    cleaned = "".join(ch for ch in extension.lower() if ch.isalnum())
    return cleaned[:10] or "bin"


class LocalFileStorage:
    """Stores each document's raw bytes at
    `<DOCUMENT_STORAGE_PATH>/<document_id>.<ext>`. The filename is always
    the document's own server-generated UUID - the original uploaded
    filename is never used to construct a path, which is what actually
    prevents path traversal here (not filename sanitization, which is
    inherently fragile - see app/services/documents/validation.py for the
    separate, defense-in-depth filename checks applied before this point).
    """

    def __init__(self, base_path: str) -> None:
        self._base_path = Path(base_path).resolve()
        self._base_path.mkdir(parents=True, exist_ok=True)

    def save(self, document_id: uuid.UUID, extension: str, content: bytes) -> str:
        storage_key = f"{document_id}.{_safe_extension(extension)}"
        path = self._resolve(storage_key)
        path.write_bytes(content)
        return storage_key

    def read(self, storage_key: str) -> bytes:
        return self._resolve(storage_key).read_bytes()

    def delete(self, storage_key: str) -> None:
        path = self._resolve(storage_key)
        path.unlink(missing_ok=True)

    def _resolve(self, storage_key: str) -> Path:
        # PurePosixPath(...).name strips any directory components a
        # (theoretically already-safe, but defense in depth) storage_key
        # might contain, so resolution can never escape _base_path.
        safe_name = PurePosixPath(storage_key).name
        return self._base_path / safe_name


_storage: FileStorage | None = None


def get_file_storage() -> FileStorage:
    global _storage
    if _storage is None:
        _storage = LocalFileStorage(get_settings().document_storage_path)
    return _storage
