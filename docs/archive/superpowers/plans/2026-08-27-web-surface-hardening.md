# Web Surface Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Close the confirmed SPA traversal, bound all document uploads, stream downloads safely, and expose meaningful liveness/readiness checks.

**Architecture:** Small helpers in app/core own path containment, upload limits, streaming, and readiness. API modules call those helpers without changing domain services or the current content-addressed storage model. Every exploit or boundary is introduced as a failing test before implementation.

**Tech Stack:** Python 3.12, FastAPI, Starlette, SQLAlchemy asyncio, PostgreSQL, pytest/httpx.

---

## File structure

- Create **backend/app/core/static_files.py**: canonical SPA path containment.
- Create **backend/app/core/uploads.py**: shared 25 MiB bounded UploadFile reader.
- Create **backend/app/core/health.py**: database and document-volume readiness checks.
- Modify **backend/app/core/storage.py**: safe key resolution and chunk iterator.
- Modify **backend/app/main.py**: safe SPA fallback and live/ready routes.
- Modify **backend/app/api/invoice_documents.py**: bounded upload/version and streamed attachment response.
- Modify **backend/app/api/mandates.py**: bounded upload and streamed attachment response.
- Modify **backend/app/services/mandate_document_service.py**: reuse the shared limit constant; remove late-only protection duplication.
- Create **backend/tests/test_main_security.py**.
- Create **backend/tests/test_upload_limits.py**.
- Create **backend/tests/test_storage_streaming.py**.
- Extend **backend/tests/test_m2_gobd_belegfluss.py** and **backend/tests/test_m_mandate_documents.py** only where an endpoint-level regression is needed.

No file currently listed as dirty overlaps this package. Re-check that statement before writing.

### Task 1: Capture the protected baseline

- [ ] **Step 1: Record the exact tree before the writer starts**

Run:

~~~bash
git status --short --branch
git diff --stat
git diff --check
git diff -- backend/app/main.py backend/app/core/storage.py \
  backend/app/api/invoice_documents.py backend/app/api/mandates.py \
  backend/app/services/mandate_document_service.py
~~~

Expected: the repository is dirty only with the already recorded user work;
the five target files above have no pre-existing diff. If any target has a
diff, stop and re-scope around it.

- [ ] **Step 2: Verify the current exploit-shaped behavior**

Run the already established production-image exploit manually only after a
local image exists. For the TDD loop, first confirm there is no existing safe
resolver:

~~~bash
rg -n "resolve_spa|is_relative_to|MAX_UPLOAD_BYTES|iter_chunks|health/ready" backend/app backend/tests
~~~

Expected: no implementation of the planned helpers.

### Task 2: Contain SPA file resolution

- [ ] **Step 1: Write failing path-containment tests**

Create **backend/tests/test_main_security.py** with these core cases:

~~~python
from pathlib import Path

from app.core.static_files import resolve_spa_file


def test_resolve_spa_file_serves_file_inside_dist(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    index = dist / "index.html"
    index.write_text("index", encoding="utf-8")
    favicon = dist / "favicon.svg"
    favicon.write_text("svg", encoding="utf-8")

    assert resolve_spa_file(dist, "favicon.svg") == favicon.resolve()


def test_resolve_spa_file_rejects_parent_traversal(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    index = dist / "index.html"
    index.write_text("index", encoding="utf-8")
    secret = tmp_path / ".env"
    secret.write_text("SECRET", encoding="utf-8")

    assert resolve_spa_file(dist, "../.env") == index.resolve()


def test_resolve_spa_file_rejects_symlink_outside_dist(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    index = dist / "index.html"
    index.write_text("index", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET", encoding="utf-8")
    (dist / "escape").symlink_to(secret)

    assert resolve_spa_file(dist, "escape") == index.resolve()
~~~

Also parameterize absolute paths, repeated parent segments, percent-decoded
parent segments, and backslash input. Expected result is always the resolved
index file, never a path outside dist.

- [ ] **Step 2: Run the test and verify RED**

Run:

~~~bash
cd backend
pytest -q tests/test_main_security.py
~~~

Expected: collection fails because app.core.static_files does not exist.

- [ ] **Step 3: Implement the minimal resolver**

Create **backend/app/core/static_files.py**:

~~~python
from pathlib import Path


def resolve_spa_file(dist_dir: str | Path, full_path: str) -> Path:
    root = Path(dist_dir).resolve(strict=True)
    index = (root / "index.html").resolve(strict=True)
    if not full_path:
        return index

    candidate = (root / full_path).resolve(strict=False)
    if candidate.is_relative_to(root) and candidate.is_file():
        return candidate
    return index
~~~

Modify the catch-all in **backend/app/main.py** to call this resolver and pass
the returned Path to FileResponse. Preserve the explicit 404 for api/ paths.

- [ ] **Step 4: Run containment tests and existing API smoke tests**

Run:

~~~bash
cd backend
pytest -q tests/test_main_security.py
pytest -q tests/test_tenant_isolation.py::TestUnauthenticatedAccess
~~~

Expected: both commands pass.

### Task 3: Bound uploads before domain services receive bytes

- [ ] **Step 1: Write failing helper boundary tests**

Create **backend/tests/test_upload_limits.py**:

~~~python
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from app.core.uploads import MAX_UPLOAD_BYTES, read_upload_limited


@pytest.mark.asyncio
async def test_bounded_upload_accepts_exact_limit():
    upload = UploadFile(filename="ok.pdf", file=BytesIO(b"x" * MAX_UPLOAD_BYTES))
    assert len(await read_upload_limited(upload)) == MAX_UPLOAD_BYTES


@pytest.mark.asyncio
async def test_bounded_upload_rejects_limit_plus_one():
    upload = UploadFile(
        filename="large.pdf",
        file=BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1)),
    )
    with pytest.raises(HTTPException) as exc:
        await read_upload_limited(upload)
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_bounded_upload_rejects_empty_file():
    upload = UploadFile(filename="empty.pdf", file=BytesIO())
    with pytest.raises(HTTPException) as exc:
        await read_upload_limited(upload)
    assert exc.value.status_code == 422
~~~

- [ ] **Step 2: Run the test and verify RED**

Run:

~~~bash
cd backend
pytest -q tests/test_upload_limits.py
~~~

Expected: collection fails because app.core.uploads does not exist.

- [ ] **Step 3: Implement the bounded reader**

Create **backend/app/core/uploads.py**:

~~~python
from fastapi import HTTPException, UploadFile

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 64 * 1024


async def read_upload_limited(
    upload: UploadFile,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="File too large (max 25 MiB)")
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=422, detail="Empty file")
    return b"".join(chunks)
~~~

Use the helper in both invoice upload paths and the mandate upload path.
Import MAX_UPLOAD_BYTES into the mandate service so its defense-in-depth check
uses exactly the same constant.

- [ ] **Step 4: Add endpoint-level oversized-file regressions**

Extend the invoice and mandate API tests with a small monkeypatch of
MAX_UPLOAD_BYTES or a helper-level limit argument so tests do not allocate
hundreds of MiB. At least one real multipart request per endpoint family must
assert 413, and existing empty-file behavior must remain 422.

- [ ] **Step 5: Run focused upload regressions**

Run:

~~~bash
cd backend
pytest -q tests/test_upload_limits.py
pytest -q tests/test_m2_gobd_belegfluss.py -k "upload or version"
pytest -q tests/test_m_mandate_documents.py
~~~

Expected: all selected tests pass.

### Task 4: Stream stored files and sanitize attachment names

- [ ] **Step 1: Write failing storage tests**

Create **backend/tests/test_storage_streaming.py**:

~~~python
from pathlib import Path

import pytest

from app.core.storage import LocalFsDocumentStore, safe_attachment_filename


def test_iter_chunks_reassembles_content(tmp_path: Path):
    store = LocalFsDocumentStore(tmp_path)
    ref = store.put("org/aa/hash.bin", b"abcdefghij", content_type="application/pdf")
    assert b"".join(store.iter_chunks(ref.uri, chunk_size=3)) == b"abcdefghij"


def test_storage_key_cannot_escape_root(tmp_path: Path):
    store = LocalFsDocumentStore(tmp_path)
    with pytest.raises(ValueError):
        list(store.iter_chunks("../secret", chunk_size=3))


def test_attachment_filename_strips_header_controls():
    assert safe_attachment_filename('evil"\r\nX-Test: yes.pdf') == "evil___X-Test_ yes.pdf"
~~~

- [ ] **Step 2: Run the test and verify RED**

Run:

~~~bash
cd backend
pytest -q tests/test_storage_streaming.py
~~~

Expected: missing iter_chunks and safe_attachment_filename failures.

- [ ] **Step 3: Extend the storage protocol safely**

Add to **backend/app/core/storage.py**:

~~~python
from collections.abc import Iterator


def safe_attachment_filename(value: str | None) -> str:
    raw = value or "document"
    return "".join(
        "_" if ch in {'"', "\\", "\r", "\n", ";", ":"} else ch
        for ch in raw
    )


class DocumentStore(Protocol):
    # existing methods remain unchanged
    def iter_chunks(self, key: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        ...
~~~

LocalFsDocumentStore must resolve every put/get/exists/iter_chunks key against
its canonical root, reject absolute or escaping paths, and yield from an rb
file handle in fixed chunks. Keep get() for extraction and integrity call
sites, but make it use the same safe resolver. Update each existing test
DocumentStore double additively with iter_chunks so the full suite remains
structurally compatible.

- [ ] **Step 4: Switch both download endpoints to real iterators**

Use StreamingResponse with store.iter_chunks(...), Content-Length, nosniff, and:

~~~python
"Content-Disposition": (
    f'attachment; filename="{safe_attachment_filename(doc.original_filename)}"'
)
~~~

Do not put the client filename into a filesystem path.

- [ ] **Step 5: Run storage and download tests**

Run:

~~~bash
cd backend
pytest -q tests/test_storage_streaming.py
pytest -q tests/test_m_mandate_documents.py -k "download"
pytest -q tests/test_m2_gobd_belegfluss.py -k "content"
~~~

Expected: all selected tests pass and downloaded bytes match the uploads.

### Task 5: Split liveness and readiness

- [ ] **Step 1: Write failing health tests**

Add to **backend/tests/test_main_security.py**:

~~~python
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_liveness_does_not_require_database():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
~~~

Add readiness unit cases that monkeypatch the DB probe and storage probe to
raise. The route must return 503 with status unavailable for either failure,
and 200 only when both probes succeed.

- [ ] **Step 2: Run health tests and verify RED**

Run:

~~~bash
cd backend
pytest -q tests/test_main_security.py -k "health or live or ready"
~~~

Expected: 404 for the new endpoints.

- [ ] **Step 3: Implement readiness helpers**

Create **backend/app/core/health.py** with:

~~~python
import os
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.core.db import get_session_factory


async def probe_database() -> None:
    async with get_session_factory()() as session:
        await session.execute(text("SELECT 1"))


def probe_document_storage() -> None:
    root = Path(settings.document_storage_root)
    root.mkdir(parents=True, exist_ok=True)
    if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        raise OSError("document storage is not readable and writable")
~~~

Expose /api/health/live and /api/health/ready in main.py. Readiness catches
probe failures, logs only the exception class/message without credentials, and
returns 503. Keep /api/health as a compatibility alias to readiness.

- [ ] **Step 4: Run health tests**

Run:

~~~bash
cd backend
pytest -q tests/test_main_security.py
~~~

Expected: all traversal and health tests pass.

### Task 6: Package verification and checkpoint

- [ ] **Step 1: Run all directly affected tests**

Run:

~~~bash
cd backend
pytest -q \
  tests/test_main_security.py \
  tests/test_upload_limits.py \
  tests/test_storage_streaming.py \
  tests/test_m2_gobd_belegfluss.py \
  tests/test_m_mandate_documents.py
~~~

Expected: all pass.

- [ ] **Step 2: Run the full backend suite**

Run:

~~~bash
cd backend
pytest -q
~~~

Expected: the complete suite passes; no new warnings treated as errors.

- [ ] **Step 3: Inspect the actual diff**

Run:

~~~bash
git diff --check
git status --short
git diff -- backend/app backend/tests
~~~

Expected: only the planned files changed in addition to the protected baseline.
No user file was overwritten.

- [ ] **Step 4: Record a no-commit checkpoint**

Do not commit. Return status, summary, changed_files, exact tests, risks, and
follow_up to the root orchestrator for independent diff review.
