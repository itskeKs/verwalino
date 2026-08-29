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
