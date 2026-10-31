"""Security and streaming tests for local document storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.storage import (
    LocalFsDocumentStore,
    attachment_content_disposition,
    safe_attachment_filename,
)


@pytest.fixture
def store(tmp_path):
    return LocalFsDocumentStore(tmp_path / "documents")


def test_storage_iter_chunks_reads_fixed_binary_chunks(store):
    store.put("tenant/aa/document.bin", b"abcdefgh", content_type="application/pdf")

    assert list(store.iter_chunks("tenant/aa/document.bin", chunk_size=3)) == [
        b"abc",
        b"def",
        b"gh",
    ]


def test_storage_iter_chunks_opens_before_iteration_and_closes_after_consumption(
    store, monkeypatch
):
    store.put("tenant/aa/document.bin", b"abcdefgh", content_type="application/pdf")
    opened = []
    original_open = Path.open

    def tracked_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", tracked_open)
    chunks = store.iter_chunks("tenant/aa/document.bin", chunk_size=3)

    assert len(opened) == 1
    assert opened[0].closed is False
    assert list(chunks) == [b"abc", b"def", b"gh"]
    assert opened[0].closed is True


@pytest.mark.parametrize(
    "key", ["", ".", "tenant/..", "../outside", "tenant/../../outside", "/etc/passwd"]
)
def test_storage_rejects_escaping_keys(store, key):
    with pytest.raises(ValueError):
        store.exists(key)


def test_safe_attachment_filename_removes_header_and_path_characters():
    assert safe_attachment_filename('invoice; bad:"name\\\r\n.pdf') == "invoice_ bad__name___.pdf"


def test_attachment_header_uses_ascii_fallback_and_rfc5987_for_unicode_names():
    header = attachment_content_disposition("über-📄.html")

    assert header.startswith('attachment; filename="')
    assert header.isascii()
    assert "filename*=UTF-8''" in header
    assert "%C3%BC" in header


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["close", "rollback", "savepoint"])
async def test_failed_cleanup_releases_the_directory_lock_and_preserves_the_error(
    store, monkeypatch, ending
):
    import errno
    import os
    import uuid
    from contextlib import ExitStack
    from types import SimpleNamespace
    from sqlalchemy.orm import Session
    from app.core import storage
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    org = uuid.uuid4()
    key = storage.make_storage_key(str(org), storage.compute_sha256(b"uncommitted"))
    store.put(key, b"uncommitted", content_type="application/pdf")
    original = RuntimeError("database write failed")

    def read_only_unlink(path, *args, **kwargs):
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(Path, "unlink", read_only_unlink)
    session = Session()  # No bind: this exercises real transaction hooks without a DB.
    session.begin()
    await storage.lock_document_storage(SimpleNamespace(info=session.info), org, store)
    handle = next(iter(session.info["storage_write_locks"].values()))
    observed = None
    try:
        with ExitStack() as contexts:
            contexts.enter_context(session)
            if ending != "close":
                contexts.enter_context(session.get_transaction())
            if ending == "savepoint":
                contexts.enter_context(session.begin_nested())
            transaction = session.get_nested_transaction() or session.get_transaction()
            session.info["uncommitted_storage"] = [(store, key, transaction)]
            raise original
    except BaseException as error:
        observed = error
    try:
        os.fstat(handle)
    except OSError as error:
        assert error.errno == errno.EBADF
        closed = True
    else:
        closed = False
        os.close(handle)  # Keep the regression's red run from leaking its descriptor.
    finally:
        session.close()
    assert closed, "Failed cleanup retained the organization directory lock"
    assert observed is original
    assert store.exists(key)  # Failed cleanup leaves the orphan for reconciliation.
