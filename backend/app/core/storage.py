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
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

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

        if path.exists():
            # Content-addressed: same hash = same content. Idempotent.
            return StoredObjectRef(
                backend="LOCAL_FS",
                uri=key,
                version=None,
                sha256=sha,
                byte_size=len(data),
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        # Make read-only (weak WORM substitute on local FS)
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

        return StoredObjectRef(
            backend="LOCAL_FS",
            uri=key,
            version=None,
            sha256=sha,
            byte_size=len(data),
        )

    def get(self, key: str) -> bytes:
        path = self._root / key
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {key}")
        return path.read_bytes()

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
