"""Authorized, integrity-checked content routes for declaration source PDFs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core import storage
from app.core.storage import compute_sha256, make_storage_key
from tests.conftest import MIGRATOR_URL


PDF_BYTES = b"%PDF-1.7\ncontent route fixture\n%%EOF"


async def _migrator_execute(statement: str, parameters: dict) -> None:
    """Make a committed setup mutation visible to the independent HTTP request."""
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(statement), parameters)
    finally:
        await engine.dispose()


async def _cleanup_content_graph(graph: "ContentGraph") -> None:
    """Remove committed fixture rows in FK order after each HTTP test."""
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM declaration_import_document "
                    "WHERE import_id = :import_id OR import_id = :foreign_import_id"
                ),
                {
                    "import_id": graph.import_id,
                    "foreign_import_id": graph.foreign_import_id,
                },
            )
            await connection.execute(
                text(
                    "DELETE FROM property_document WHERE "
                    "id = :document_id OR id = :other_property_document_id OR "
                    "id = :import_document_id OR id = :archived_document_id OR "
                    "id = :foreign_document_id"
                ),
                {
                    "document_id": graph.document_id,
                    "other_property_document_id": graph.other_property_document_id,
                    "import_document_id": graph.import_document_id,
                    "archived_document_id": graph.archived_document_id,
                    "foreign_document_id": graph.foreign_document_id,
                },
            )
            await connection.execute(
                text(
                    "DELETE FROM declaration_import "
                    "WHERE id = :import_id OR id = :foreign_import_id"
                ),
                {
                    "import_id": graph.import_id,
                    "foreign_import_id": graph.foreign_import_id,
                },
            )
            await connection.execute(
                text(
                    "DELETE FROM property WHERE "
                    "id = :property_id OR id = :other_property_id OR id = :foreign_property_id"
                ),
                {
                    "property_id": graph.property_id,
                    "other_property_id": graph.other_property_id,
                    "foreign_property_id": graph.foreign_property_id,
                },
            )
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class ContentGraph:
    property_id: uuid.UUID
    other_property_id: uuid.UUID
    document_id: uuid.UUID
    other_property_document_id: uuid.UUID
    import_id: uuid.UUID
    import_document_id: uuid.UUID
    archived_document_id: uuid.UUID
    foreign_property_id: uuid.UUID
    foreign_document_id: uuid.UUID
    foreign_import_id: uuid.UUID
    storage_uri: str
    sha256: str


@pytest.fixture(autouse=True)
def isolated_document_store(tmp_path):
    """Use content-addressed local objects and reset the process-global store."""
    document_store = storage.LocalFsDocumentStore(tmp_path)
    storage.set_store(document_store)
    yield document_store
    storage._store = None


@pytest_asyncio.fixture
async def content_graph(
    migrator_session: AsyncSession,
    seed_data: dict,
    isolated_document_store: storage.LocalFsDocumentStore,
) -> ContentGraph:
    org_a = seed_data["org_a_id"]
    org_b = seed_data["org_b_id"]
    sha256 = compute_sha256(PDF_BYTES)
    storage_uri = make_storage_key(str(org_a), sha256)
    isolated_document_store.put(storage_uri, PDF_BYTES, content_type="application/pdf")

    property_id = uuid.uuid4()
    other_property_id = uuid.uuid4()
    foreign_property_id = uuid.uuid4()
    document_id = uuid.uuid4()
    other_property_document_id = uuid.uuid4()
    import_id = uuid.uuid4()
    import_document_id = uuid.uuid4()
    archived_document_id = uuid.uuid4()
    foreign_document_id = uuid.uuid4()
    foreign_import_id = uuid.uuid4()

    await migrator_session.execute(
        text(
            "INSERT INTO property (id, organization_id, name, management_type) "
            "VALUES (:id, :organization_id, :name, 'WEG')"
        ),
        [
            {"id": property_id, "organization_id": org_a, "name": "Content object"},
            {"id": other_property_id, "organization_id": org_a, "name": "Other object"},
            {"id": foreign_property_id, "organization_id": org_b, "name": "Foreign object"},
        ],
    )
    await migrator_session.execute(
        text(
            "INSERT INTO declaration_import ("
            "id, organization_id, mode, status, initiated_by, max_cost_usd"
            ") VALUES (:id, :organization_id, 'NEW_PROPERTY', 'DRAFT', :initiated_by, 10)"
        ),
        [
            {
                "id": import_id,
                "organization_id": org_a,
                "initiated_by": seed_data["user_a_admin_id"],
            },
            {
                "id": foreign_import_id,
                "organization_id": org_b,
                "initiated_by": seed_data["user_b_admin_id"],
            },
        ],
    )
    document_rows = [
        {
            "id": document_id,
            "organization_id": org_a,
            "property_id": property_id,
            "title": "Teilung",
            "filename": "../../Teilung; sicher.pdf",
            "uri": storage_uri,
            "sha": sha256,
            "size": len(PDF_BYTES),
            "archived_at": None,
        },
        {
            "id": other_property_document_id,
            "organization_id": org_a,
            "property_id": other_property_id,
            "title": "Other",
            "filename": "other.pdf",
            "uri": storage_uri,
            "sha": sha256,
            "size": len(PDF_BYTES),
            "archived_at": None,
        },
        {
            "id": import_document_id,
            "organization_id": org_a,
            "property_id": None,
            "title": "Draft",
            "filename": "draft.pdf",
            "uri": storage_uri,
            "sha": sha256,
            "size": len(PDF_BYTES),
            "archived_at": None,
        },
        {
            "id": archived_document_id,
            "organization_id": org_a,
            "property_id": property_id,
            "title": "Archived",
            "filename": "archived.pdf",
            "uri": storage_uri,
            "sha": sha256,
            "size": len(PDF_BYTES),
            "archived_at": "now()",
        },
        {
            "id": foreign_document_id,
            "organization_id": org_b,
            "property_id": foreign_property_id,
            "title": "Foreign",
            "filename": "foreign.pdf",
            "uri": storage_uri,
            "sha": sha256,
            "size": len(PDF_BYTES),
            "archived_at": None,
        },
    ]
    for row in document_rows:
        archived_sql = "now()" if row.pop("archived_at") else "NULL"
        await migrator_session.execute(
            text(
                "INSERT INTO property_document ("
                "id, organization_id, property_id, doc_type, title, original_filename, "
                "mime_type, content_byte_size, content_sha256, storage_uri, "
                "storage_backend, page_count, archived_at"
                ") VALUES ("
                ":id, :organization_id, :property_id, 'DECLARATION', :title, :filename, "
                "'application/pdf', :size, :sha, :uri, 'LOCAL_FS', 1, "
                f"{archived_sql})"
            ),
            row,
        )
    await migrator_session.execute(
        text(
            "INSERT INTO declaration_import_document ("
            "id, organization_id, import_id, document_id, sequence_no, document_role"
            ") VALUES (:id, :organization_id, :import_id, :document_id, 1, 'DECLARATION')"
        ),
        [
            {
                "id": uuid.uuid4(),
                "organization_id": org_a,
                "import_id": import_id,
                "document_id": import_document_id,
            },
            {
                "id": uuid.uuid4(),
                "organization_id": org_b,
                "import_id": foreign_import_id,
                "document_id": foreign_document_id,
            },
        ],
    )
    await migrator_session.commit()
    graph = ContentGraph(
        property_id=property_id,
        other_property_id=other_property_id,
        document_id=document_id,
        other_property_document_id=other_property_document_id,
        import_id=import_id,
        import_document_id=import_document_id,
        archived_document_id=archived_document_id,
        foreign_property_id=foreign_property_id,
        foreign_document_id=foreign_document_id,
        foreign_import_id=foreign_import_id,
        storage_uri=storage_uri,
        sha256=sha256,
    )
    try:
        yield graph
    finally:
        await _cleanup_content_graph(graph)


@pytest.mark.asyncio
async def test_property_listing_and_content_are_readonly_safe(
    client_a_readonly, content_graph: ContentGraph
) -> None:
    listed = await client_a_readonly.get(f"/api/properties/{content_graph.property_id}/documents")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [str(content_graph.document_id)]
    assert "storage_uri" not in listed.json()[0]
    assert "storage_backend" not in listed.json()[0]

    response = await client_a_readonly.get(
        f"/api/properties/{content_graph.property_id}/documents/{content_graph.document_id}/content?page=7",
        headers={"Range": "bytes=0-7"},
    )
    assert response.status_code == 200
    assert response.content == PDF_BYTES
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-length"] == str(len(PDF_BYTES))
    assert response.headers["etag"] == f'"{content_graph.sha256}"'
    assert response.headers["content-disposition"] == 'inline; filename="Teilung_ sicher.pdf"'
    assert "accept-ranges" not in response.headers


@pytest.mark.asyncio
async def test_content_routes_hide_unbound_archived_foreign_and_other_property_documents(
    client_a_readonly, content_graph: ContentGraph
) -> None:
    property_base = f"/api/properties/{content_graph.property_id}/documents"
    for document_id in (
        content_graph.import_document_id,
        content_graph.other_property_document_id,
        content_graph.archived_document_id,
        content_graph.foreign_document_id,
    ):
        response = await client_a_readonly.get(f"{property_base}/{document_id}/content")
        assert response.status_code == 404

    foreign_property = await client_a_readonly.get(
        f"/api/properties/{content_graph.foreign_property_id}/documents"
    )
    assert foreign_property.status_code == 404


@pytest.mark.asyncio
async def test_import_draft_content_is_writer_only_and_status_limited(
    client_a_admin, client_a_readonly, content_graph: ContentGraph
) -> None:
    path = (
        f"/api/declaration-imports/{content_graph.import_id}/documents/"
        f"{content_graph.import_document_id}/content"
    )
    assert (await client_a_readonly.get(path)).status_code == 403
    response = await client_a_admin.get(path)
    assert response.status_code == 200
    assert response.content == PDF_BYTES

    await _migrator_execute(
        "UPDATE declaration_import SET status = 'NEEDS_REVIEW' WHERE id = :id",
        {"id": content_graph.import_id},
    )
    assert (await client_a_admin.get(path)).status_code == 200

    await _migrator_execute(
        "UPDATE declaration_import SET status = 'QUEUED' WHERE id = :id",
        {"id": content_graph.import_id},
    )
    assert (await client_a_admin.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_import_content_status_is_checked_with_its_document_link(
    client_a_admin, content_graph: ContentGraph
) -> None:
    """A non-review status cannot pass a stale preliminary import lookup."""
    await _migrator_execute(
        "UPDATE declaration_import SET status = 'FAILED' WHERE id = :id",
        {"id": content_graph.import_id},
    )
    response = await client_a_admin.get(
        f"/api/declaration-imports/{content_graph.import_id}/documents/"
        f"{content_graph.import_document_id}/content"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_import_content_requires_exact_document_link(
    client_a_admin, content_graph: ContentGraph
) -> None:
    response = await client_a_admin.get(
        f"/api/declaration-imports/{content_graph.import_id}/documents/"
        f"{content_graph.document_id}/content"
    )
    assert response.status_code == 404
    foreign_response = await client_a_admin.get(
        f"/api/declaration-imports/{content_graph.foreign_import_id}/documents/"
        f"{content_graph.foreign_document_id}/content"
    )
    assert foreign_response.status_code == 404


@pytest.mark.asyncio
async def test_missing_or_corrupt_content_fails_before_response(
    client_a_readonly,
    content_graph: ContentGraph,
    isolated_document_store: storage.LocalFsDocumentStore,
) -> None:
    path = f"/api/properties/{content_graph.property_id}/documents/{content_graph.document_id}/content"
    # Same length proves SHA verification is independent of the size check.
    isolated_document_store.put(
        content_graph.storage_uri,
        b"X" + PDF_BYTES[1:],
        content_type="application/pdf",
    )
    sha_response = await client_a_readonly.get(path)
    assert sha_response.status_code == 409
    assert sha_response.json()["detail"] == "DOCUMENT_INTEGRITY_FAILED"

    # A short object exercises the exact byte-count guard as well.
    isolated_document_store.put(content_graph.storage_uri, b"%PDF-short", content_type="application/pdf")
    size_response = await client_a_readonly.get(path)
    assert size_response.status_code == 409
    assert size_response.json()["detail"] == "DOCUMENT_INTEGRITY_FAILED"

    class MissingStore:
        def get(self, key: str) -> bytes:
            assert key == content_graph.storage_uri
            raise FileNotFoundError(key)

    storage.set_store(MissingStore())
    missing_response = await client_a_readonly.get(path)
    assert missing_response.status_code == 409
    assert missing_response.json()["detail"] == "DOCUMENT_INTEGRITY_FAILED"


@pytest.mark.asyncio
async def test_content_reads_the_persisted_uri_exactly_once(
    client_a_readonly, content_graph: ContentGraph
) -> None:
    class CountingStore:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def get(self, key: str) -> bytes:
            self.keys.append(key)
            return PDF_BYTES

    document_store = CountingStore()
    storage.set_store(document_store)
    response = await client_a_readonly.get(
        f"/api/properties/{content_graph.property_id}/documents/"
        f"{content_graph.document_id}/content?page=1"
    )
    assert response.status_code == 200
    assert document_store.keys == [content_graph.storage_uri]
