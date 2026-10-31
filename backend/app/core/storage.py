"""Document storage infrastructure -- content-addressed, tenant-partitioned.

Design (m2-gobd-belegfluss.md section 5, SDD section 16):
  - Thin, **swappable** abstraction behind a Protocol (same pattern as pdf.py).
  - Default: LocalFsDocumentStore (filesystem, content-addressed by SHA-256).
  - Retained originals are immutable; unprotected, unreferenced bytes may only
    be removed after their metadata deletion commits.
  - Later: S3DocumentStore with Object-Lock/WORM.

Content-addressing: path = <root>/<org_id>/<sha256[:2]>/<sha256>.bin
  - Identical content = identical hash = same path (idempotent writes).
  - Different content = different hash = different path.
  - Tenant partition in path = defense-in-depth against cross-org file access.
"""

from __future__ import annotations

import hashlib
import fcntl
import logging
import os
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable
from urllib.parse import quote

from sqlalchemy import event
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

logger = logging.getLogger(__name__)


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
    Removal is restricted to originals whose unprotected last reference was
    successfully deleted; this is not a retention override.
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

    def remove_unreferenced(self, key: str) -> None:
        """Unlink only after an unprotected last-reference deletion commits."""
        ...

    def iter_chunks(self, key: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        """Yield a document as fixed-size binary chunks."""
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
        self._root = Path(root).resolve()

    def _resolve_key(self, key: str) -> Path:
        """Resolve a document key without permitting filesystem traversal."""
        candidate_key = Path(key)
        if (
            not key
            or candidate_key.is_absolute()
            or "\\" in key
            or any(part in {".", ".."} for part in key.split("/"))
        ):
            raise ValueError("Invalid document storage key")
        candidate = (self._root / candidate_key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("Invalid document storage key") from exc
        return candidate

    def put(self, key: str, data: bytes, *, content_type: str) -> StoredObjectRef:
        path = self._resolve_key(key)
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
        path = self._resolve_key(key)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {key}")
        return path.read_bytes()

    def iter_bytes(self, key: str) -> Iterator[bytes]:
        return self.iter_chunks(key)

    def exists(self, key: str) -> bool:
        return self._resolve_key(key).exists()

    def organization_bytes(self, org_id: str) -> int:
        """Inspect physical usage for offline administrative reconciliation only."""
        directory = self._resolve_key(org_id)
        if not directory.exists():
            return 0
        return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())

    def discard_uncommitted(self, key: str) -> None:
        """Remove only an upload tracked as newly created by a failed transaction."""
        self._resolve_key(key).unlink(missing_ok=True)

    def remove_unreferenced(self, key: str) -> None:
        path = self._resolve_key(key)
        path.unlink(missing_ok=True)
        if path.parent.exists():
            self._fsync_parent_directory(path.parent)

    def iter_chunks(self, key: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        """Yield a stored document without loading it fully into memory."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        path = self._resolve_key(key)
        try:
            handle = path.open("rb")
        except FileNotFoundError:
            raise FileNotFoundError(f"Document not found: {key}") from None

        def chunks() -> Iterator[bytes]:
            try:
                while chunk := handle.read(chunk_size):
                    yield chunk
            finally:
                handle.close()

        return chunks()


_ATTACHMENT_FILENAME_TRANSLATION = str.maketrans(
    {character: "_" for character in ('"', "\\", "\r", "\n", ";", ":")}
)


def safe_attachment_filename(filename: str) -> str:
    """Return a filename safe for a quoted Content-Disposition header."""
    return "".join(
        "_" if ord(character) < 32 or ord(character) == 127 else character
        for character in filename.translate(_ATTACHMENT_FILENAME_TRANSLATION)
    )


def attachment_content_disposition(filename: str) -> str:
    """Build an attachment header with ASCII fallback and RFC 5987 Unicode name."""
    safe_filename = safe_attachment_filename(filename) or "download"
    ascii_filename = unicodedata.normalize("NFKD", safe_filename).encode(
        "ascii", "ignore"
    ).decode("ascii")
    ascii_filename = safe_attachment_filename(ascii_filename) or "download"
    encoded_filename = quote(safe_filename, safe="!#$&+-.^_`|~")
    return (
        f'attachment; filename="{ascii_filename}"; '
        f"filename*=UTF-8''{encoded_filename}"
    )


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


def retain_document(session, reference: StoredObjectRef) -> None:
    """Keep an original once an independent commit may reference it."""
    pending = session.info.get("uncommitted_storage")
    if pending is not None:
        session.info["uncommitted_storage"] = [item for item in pending if item[1] != reference.uri]


def open_storage_lock(store: LocalFsDocumentStore, org_id) -> int:
    """Lock the shared organization directory without creating lock files.

    The directory inode is shared by web/worker containers mounting the same
    document volume. Private /tmp files would not coordinate those containers;
    deleting lock files would strand waiters on stale inodes.
    """
    directory = store._resolve_key(str(org_id))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))


async def lock_document_storage(session, org_id, store: DocumentStore) -> bool:
    from app.core.quotas import get_quota_plan
    if get_quota_plan().name == "unlimited" or not isinstance(store, LocalFsDocumentStore):
        return False
    locks = session.info.setdefault("storage_write_locks", {})
    lock_key = (store._root, org_id)
    if lock_key not in locks:
        handle = open_storage_lock(store, org_id)
        try:
            await run_in_threadpool(fcntl.flock, handle, fcntl.LOCK_EX)
        except BaseException:
            os.close(handle)
            raise
        locks[lock_key] = handle
    return True


def remove_document_after_commit(session, store: DocumentStore, key: str) -> None:
    """Queue only after the repository accepted an unprotected last-ref delete."""
    transaction = session.sync_session.get_nested_transaction() or session.sync_session.get_transaction()
    session.info.setdefault("deleted_storage", []).append((store, key, transaction))


@event.listens_for(Session, "after_commit")
def _commit_storage(session):
    pending = session.info.get("uncommitted_storage", [])
    nested = session.get_nested_transaction()
    if nested is not None:
        session.info["uncommitted_storage"] = [
            (store, key, nested.parent if transaction is nested else transaction)
            for store, key, transaction in pending
        ]
    else:
        session.info.pop("uncommitted_storage", None)
    removals = session.info.pop("deleted_storage", [])
    if nested is not None:
        session.info["deleted_storage"] = [
            (store, key, nested.parent if transaction is nested else transaction)
            for store, key, transaction in removals
        ]
    else:
        for store, key, _transaction in removals:
            try:
                store.remove_unreferenced(key)
            except Exception:
                logger.warning("Post-commit document removal failed for %s; original retained for reconciliation", key, exc_info=True)


@event.listens_for(Session, "after_soft_rollback")
def _rollback_storage(session, previous_transaction):
    if previous_transaction.parent is not None and not previous_transaction.nested:
        previous_transaction = previous_transaction.parent
    pending = session.info.pop("uncommitted_storage", [])
    keep = []
    for store, key, transaction in pending:
        if transaction is previous_transaction:
            _discard_uncommitted(store, key)
        else:
            keep.append((store, key, transaction))
    if keep:
        session.info["uncommitted_storage"] = keep
    session.info["deleted_storage"] = [
        item for item in session.info.get("deleted_storage", [])
        if item[2] is not previous_transaction
    ]


def _discard_uncommitted(store, key):
    try:
        store.discard_uncommitted(key)
    except Exception:
        logger.warning("Rollback document removal failed for %s; original retained for reconciliation", key, exc_info=True)


@event.listens_for(Session, "after_transaction_end")
def _close_storage(session, transaction):
    # Session.close also rolls back, without emitting after_soft_rollback.
    if transaction.parent is None:
        session.info.pop("deleted_storage", None)
        try:
            for store, key, _transaction in session.info.pop("uncommitted_storage", []):
                _discard_uncommitted(store, key)
        finally:
            for handle in session.info.pop("storage_write_locks", {}).values():
                os.close(handle)


async def put_document(session, org_id, data: bytes, *, content_type: str,
                       store: DocumentStore | None = None) -> StoredObjectRef:
    """Check before writing; keep only originals belonging to committed writes."""
    from app.core.quotas import get_quota_plan
    from app.services.quota_service import lock_quota, require_document_storage, require_quota

    active_store = store or get_store()
    plan = get_quota_plan()
    require_quota(0, "upload_bytes", len(data), plan=plan)
    key = make_storage_key(str(org_id), compute_sha256(data))
    if plan.storage_bytes is not None:
        await lock_quota(session, org_id)
    # DB locks release before rollback callbacks; retain the hosted filesystem
    # lock through cleanup so a committed duplicate cannot lose its bytes.
    await lock_document_storage(session, org_id, active_store)
    session.info["deleted_storage"] = [
        item for item in session.info.get("deleted_storage", []) if item[1] != key
    ]
    existed = active_store.exists(key)
    if plan.storage_bytes is not None:
        # Prior writes in this transaction must be visible to the aggregate,
        # including unflushed ORM metadata in multi-document operations.
        await session.flush()
        await require_document_storage(session, org_id, key, len(data), plan=plan)
    if plan.storage_bytes is not None and not existed and isinstance(active_store, LocalFsDocumentStore):
        transaction = session.sync_session.get_nested_transaction() or session.sync_session.get_transaction()
        session.info.setdefault("uncommitted_storage", []).append((active_store, key, transaction))
    return await run_in_threadpool(active_store.put, key, data, content_type=content_type)


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
