"""Document storage infrastructure -- content-addressed, tenant-partitioned.

Design (m2-gobd-belegfluss.md section 5, SDD section 16):
  - Thin, **swappable** abstraction behind a Protocol (same pattern as pdf.py).
  - Default: LocalFsDocumentStore (filesystem, content-addressed by SHA-256).
  - No delete() in the protocol -- documents are never deleted (GoBD).
  - Later: S3DocumentStore with Object-Lock/WORM.

Content-addressing: path = <root>/<org_id>/<sha256[:2]>/<sha256>.bin
  - Identical content = identical hash = same path (idempotent writes).
  - Different content = different hash = different path.
  - Tenant partition in path = defense-in-depth against cross-org file access.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from app.core.config import settings


@dataclass(frozen=True)
class StoredObjectRef:
    """Reference to a stored object -- returned by put(), stored in invoice_document."""

    backend: str  # "LOCAL_FS" | "S3"
    uri: str  # -> invoice_document.storage_uri
    version: str | None  # -> invoice_document.storage_version
    sha256: str  # hex digest for cross-validation against DB
    byte_size: int


@runtime_checkable
class DocumentStore(Protocol):
    """Protocol for document storage backends.

    Implementations must be stateless and thread-safe.
    Deliberately NO delete() -- documents are never deleted in Phase 1 (GoBD).
    A later retention-governed removal (M9) is a separate, privileged operation.
    """

    def put(self, key: str, data: bytes, *, content_type: str) -> StoredObjectRef:
        """Store a document. key = content-addressed path (org_id/sha-prefix/sha.bin)."""
        ...

    def get(self, key: str) -> bytes:
        """Retrieve document bytes by key. Raises FileNotFoundError if missing."""
        ...

    def iter_bytes(self, key: str) -> Iterator[bytes]:
        """Yield stored content in bounded chunks."""
        ...

    def exists(self, key: str) -> bool:
        """Check if a key exists in the store."""
        ...


def compute_sha256(data: bytes) -> str:
    """Compute SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def make_storage_key(org_id: str, sha256_hex: str) -> str:
    """Build the content-addressed, tenant-partitioned storage key.

    Format: <org_id>/<sha256[:2]>/<sha256>.bin
    """
    return f"{org_id}/{sha256_hex[:2]}/{sha256_hex}.bin"


class LocalFsDocumentStore:
    """Local filesystem store -- content-addressed, tenant-partitioned.

    Files are made read-only after writing as a weak WORM substitute.
    The real immutability is enforced by the DB triggers (ADR-0003)
    plus later S3 Object-Lock.
    """

    def __init__(self, root: str | Path):
        self._root = Path(root)

    def put(self, key: str, data: bytes, *, content_type: str) -> StoredObjectRef:
        path = self._root / key
        sha = compute_sha256(data)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._harden_parent_directories(path)

        if self._matches_expected_content(path, sha, len(data)):
            # Content-addressed: same hash = same content. Idempotent.
            path.chmod(stat.S_IRUSR)
            self._fsync_parent_directory(path.parent)
            return StoredObjectRef(
                backend="LOCAL_FS",
                uri=key,
                version=None,
                sha256=sha,
                byte_size=len(data),
            )

        self._atomic_replace(path, data)

        return StoredObjectRef(
            backend="LOCAL_FS",
            uri=key,
            version=None,
            sha256=sha,
            byte_size=len(data),
        )

    def _harden_parent_directories(self, path: Path) -> None:
        directory = self._root
        directory.chmod(stat.S_IRWXU)
        for component in path.parent.relative_to(self._root).parts:
            directory = directory / component
            directory.chmod(stat.S_IRWXU)

    @staticmethod
    def _matches_expected_content(path: Path, expected_sha256: str, expected_size: int) -> bool:
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as file_handle:
                while chunk := file_handle.read(64 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
        except FileNotFoundError:
            return False
        return size == expected_size and digest.hexdigest() == expected_sha256

    @staticmethod
    def _atomic_replace(path: Path, data: bytes) -> None:
        raw_file_descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(raw_file_descriptor, "wb") as file_handle:
                raw_file_descriptor = None
                file_handle.write(data)
                file_handle.flush()
                os.fchmod(file_handle.fileno(), stat.S_IRUSR)
                os.fsync(file_handle.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
            LocalFsDocumentStore._fsync_parent_directory(path.parent)
        finally:
            if raw_file_descriptor is not None:
                os.close(raw_file_descriptor)
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _fsync_parent_directory(directory: Path) -> None:
        if os.name == "nt":
            # Windows does not provide the POSIX directory-fsync durability contract.
            return
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_file_descriptor = os.open(directory, directory_flags)
        try:
            os.fsync(directory_file_descriptor)
        finally:
            os.close(directory_file_descriptor)

    def get(self, key: str) -> bytes:
        path = self._root / key
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {key}")
        return path.read_bytes()

    def iter_bytes(self, key: str) -> Iterator[bytes]:
        path = self._root / key
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {key}")
        with path.open("rb") as file_handle:
            while chunk := file_handle.read(64 * 1024):
                yield chunk

    def exists(self, key: str) -> bool:
        return (self._root / key).exists()


# --- Module-level singleton (the active store) ---

_store: DocumentStore | None = None


def get_store() -> DocumentStore:
    """Return the currently configured document store.

    Lazy-initializes to LocalFsDocumentStore if not explicitly set.
    """
    global _store
    if _store is None:
        root = getattr(settings, "document_storage_root", None) or "/tmp/hv-documents"
        _store = LocalFsDocumentStore(root)
    return _store


def set_store(store: DocumentStore) -> None:
    """Replace the active store (useful for testing or backend swap)."""
    global _store
    _store = store


def verify_document_integrity(
    content_sha256: str,
    storage_uri: str,
    store: DocumentStore | None = None,
) -> bool:
    """Verify that a stored document matches its recorded SHA-256 hash.

    Args:
        content_sha256: Expected SHA-256 hex digest (from invoice_document row).
        storage_uri: Storage key (from invoice_document.storage_uri).
        store: Optional store override (defaults to get_store()).

    Returns:
        True if the file exists and its hash matches; False otherwise.
    """
    if store is None:
        store = get_store()
    try:
        data = store.get(storage_uri)
    except FileNotFoundError:
        return False
    actual_sha = compute_sha256(data)
    return actual_sha == content_sha256


def verify_document_integrity_and_size(
    expected_sha256: str,
    expected_size: int,
    storage_uri: str,
    store: DocumentStore | None = None,
) -> bool:
    """Verify a stored document without materializing its entire content."""
    active_store = store or get_store()
    digest = hashlib.sha256()
    size = 0
    try:
        for chunk in active_store.iter_bytes(storage_uri):
            digest.update(chunk)
            size += len(chunk)
    except FileNotFoundError:
        return False
    return size == expected_size and digest.hexdigest() == expected_sha256
