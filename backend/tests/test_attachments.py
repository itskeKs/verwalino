"""T36 general document metadata, retained originals and access boundaries."""
import pytest

pytestmark = pytest.mark.anyio


async def test_attachment_list_requires_authentication(client_unauth):
    response = await client_unauth.get("/api/attachments")
    assert response.status_code == 401


async def test_attachment_list_for_authenticated_member(client_a_admin):
    response = await client_a_admin.get("/api/attachments")
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_offset": None}


async def test_attachment_upload_rejects_readonly_member(client_a_readonly):
    response = await client_a_readonly.post(
        "/api/attachments",
        data={"property_id": "00000000-0000-0000-0000-000000000036", "title": "Test"},
        files={"file": ("test.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert response.status_code == 403

from hashlib import sha256
from app.core.pdf import render_pdf
from app.core.storage import LocalFsDocumentStore, make_storage_key
from app.services import attachment_service
from tests.test_meters_api import meter_world as _meter_world

attachment_world = _meter_world


@pytest.fixture
def attachment_store(tmp_path, monkeypatch):
    store = LocalFsDocumentStore(tmp_path)
    monkeypatch.setattr(attachment_service, "get_store", lambda: store)
    return store


@pytest.fixture(scope="session")
def attachment_pdf():
    return render_pdf("<html><body><p>T36 retained original</p></body></html>")


async def upload(client, world, content, **changes):
    body = {"property_id": world["property"], "title": "Original", **changes}
    return await client.post("/api/attachments", data=body,
                            files={"file": ("Prüfung.pdf", content, "application/pdf")})


async def created_attachment(client, world, content, **changes):
    response = await upload(client, world, content, **changes)
    assert response.status_code == 201, response.text
    return response.json()


async def test_attachment_upload_download_archive_retains_original(
        client_a_admin, attachment_world, attachment_store, attachment_pdf):
    row = await created_attachment(client_a_admin, attachment_world, attachment_pdf,
                                   unit_id=attachment_world["unit"])
    assert "storage_uri" not in row and "storage_backend" not in row
    assert row["content_sha256"] == sha256(attachment_pdf).hexdigest()
    assert row["content_byte_size"] == len(attachment_pdf)
    path = f"/api/attachments/{row['id']}"
    response = await client_a_admin.get(path + "/download?inline=true")
    assert response.status_code == 200, response.text
    assert response.content == attachment_pdf
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert "filename*=UTF-8''Pr%C3%BCfung.pdf" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    listed = await client_a_admin.get("/api/attachments", params={"property_id": attachment_world["property"]})
    assert [x["id"] for x in listed.json()["items"]] == [row["id"]]
    archived = await client_a_admin.post(path + "/archive", json={"reason": "Replaced by reviewed copy"})
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] and archived.json()["archived_by"]
    for key in row:
        if key not in {"archived_at", "archived_by", "archive_reason"}:
            assert archived.json()[key] == row[key]
    assert (await client_a_admin.post(path + "/archive", json={"reason": "Replaced by reviewed copy"})).json() == archived.json()
    assert (await client_a_admin.post(path + "/archive", json={"reason": "Different reason"})).status_code == 409
    assert (await client_a_admin.get("/api/attachments", params={"property_id": attachment_world["property"]})).json()["items"] == []
    assert [x["id"] for x in (await client_a_admin.get("/api/attachments", params={
        "property_id": attachment_world["property"], "include_archived": True})).json()["items"]] == [row["id"]]
    assert (await client_a_admin.get(path + "/download")).content == attachment_pdf
    assert (await client_a_admin.delete(path)).status_code == 405


async def test_attachment_pagination_includes_unit_documents_under_property(
        client_a_admin, attachment_world, attachment_store, attachment_pdf):
    rows = [await created_attachment(client_a_admin, attachment_world, attachment_pdf,
             **({"unit_id": attachment_world["unit"]} if i else {})) for i in range(3)]
    first = (await client_a_admin.get("/api/attachments", params={"property_id": attachment_world["property"], "limit": 2})).json()
    assert len(first["items"]) == 2 and first["next_offset"] == 2
    second = (await client_a_admin.get("/api/attachments", params={"property_id": attachment_world["property"], "limit": 2, "offset": 2})).json()
    assert second["next_offset"] is None
    assert {x["id"] for x in first["items"] + second["items"]} == {x["id"] for x in rows}
    units = (await client_a_admin.get("/api/attachments", params={"unit_id": attachment_world["unit"]})).json()
    assert {x["id"] for x in units["items"]} == {rows[1]["id"], rows[2]["id"]}


async def test_attachment_bidirectional_org_isolation_and_parent_coherence(
        client_a_admin, client_b_admin, attachment_world, attachment_store, attachment_pdf):
    own = await created_attachment(client_a_admin, attachment_world, attachment_pdf)
    other = await created_attachment(client_b_admin, attachment_world, attachment_pdf,
                                     property_id=attachment_world["foreign_property"])
    for client, hidden in ((client_a_admin, other), (client_b_admin, own)):
        path = f"/api/attachments/{hidden['id']}"
        for suffix in ("", "/download"):
            assert (await client.get(path + suffix)).status_code == 404
        assert (await client.post(path + "/archive", json={"reason": "Denied"})).status_code == 404
        assert hidden["id"] not in {x["id"] for x in (await client.get("/api/attachments")).json()["items"]}
    assert (await upload(client_a_admin, attachment_world, attachment_pdf,
        property_id=attachment_world["foreign_property"])).status_code == 404
    assert (await upload(client_a_admin, attachment_world, attachment_pdf,
        unit_id=attachment_world["foreign_unit"])).status_code == 404
    assert (await upload(client_a_admin, attachment_world, attachment_pdf,
        unit_id=attachment_world["other_unit"])).status_code == 422
    assert (await client_a_admin.get("/api/attachments", params={"property_id": attachment_world["foreign_property"]})).status_code == 404


@pytest.mark.parametrize("filename", ["../escape.pdf", "bad\\path.pdf", ".", "x" * 256])
async def test_attachment_rejects_unsafe_filename(client_a_admin, attachment_world,
                                                attachment_store, attachment_pdf, filename):
    response = await client_a_admin.post("/api/attachments", data={"property_id": attachment_world["property"]},
        files={"file": (filename, attachment_pdf, "application/pdf")})
    assert response.status_code == 422, response.text


async def test_attachment_active_content_is_never_inline(client_a_admin, attachment_world, attachment_store):
    content = b"<html><script>alert(document.cookie)</script></html>"
    row = await created_attachment(client_a_admin, attachment_world, content)
    assert row["mime_type"] == "application/octet-stream"
    response = await client_a_admin.get(f"/api/attachments/{row['id']}/download?inline=true")
    assert response.content == content
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_attachment_corrupted_original_is_not_returned(client_a_admin, attachment_world,
        attachment_store, attachment_pdf, seed_data):
    row = await created_attachment(client_a_admin, attachment_world, attachment_pdf)
    key = make_storage_key(str(seed_data["org_a_id"]), row["content_sha256"])
    target = attachment_store._root / key
    target.chmod(0o600)
    target.write_bytes(b"corruption")
    response = await client_a_admin.get(f"/api/attachments/{row['id']}/download")
    assert response.status_code == 409
    assert b"corruption" not in response.content


@pytest.mark.parametrize("body", [{}, {"reason": "   "}, {"reason": "x", "property_id": "forbidden"}])
async def test_attachment_archive_schema(client_a_admin, attachment_world, attachment_store, attachment_pdf, body):
    row = await created_attachment(client_a_admin, attachment_world, attachment_pdf)
    assert (await client_a_admin.post(f"/api/attachments/{row['id']}/archive", json=body)).status_code == 422


async def test_attachment_empty_and_size_limits(client_a_admin, attachment_world, attachment_store, monkeypatch):
    assert (await upload(client_a_admin, attachment_world, b"")).status_code == 422
    from app.api import attachments
    monkeypatch.setattr(attachments, "MAX_UPLOAD_BYTES", 16)
    assert (await upload(client_a_admin, attachment_world, b"x" * 17)).status_code == 413


async def test_attachment_service_rejects_literal_header_control_characters():
    import uuid
    from fastapi import HTTPException
    service = attachment_service.AttachmentService(None, uuid.uuid4(), uuid.uuid4())
    with pytest.raises(HTTPException) as caught:
        await service.upload(uuid.uuid4(), b"content", original_filename="bad\r\nHeader.pdf")
    assert caught.value.status_code == 422


async def test_attachment_transport_encoded_filename_cannot_inject_header(
        client_a_admin, attachment_world, attachment_store, attachment_pdf):
    response = await client_a_admin.post("/api/attachments", data={"property_id": attachment_world["property"]},
        files={"file": ("bad\r\nHeader.pdf", attachment_pdf, "application/pdf")})
    assert response.status_code == 201, response.text
    assert response.json()["original_filename"] == "bad%0D%0AHeader.pdf"
    download = await client_a_admin.get(f"/api/attachments/{response.json()['id']}/download")
    header = download.headers["content-disposition"]
    assert "\r" not in header and "\n" not in header
    assert "filename*=UTF-8''bad%250D%250AHeader.pdf" in header
    assert download.content == attachment_pdf


@pytest.mark.parametrize("field", ["title", "archive_reason"])
async def test_attachment_rejects_nul_text(client_a_admin, attachment_world,
        attachment_store, attachment_pdf, field):
    if field == "title":
        result = await upload(client_a_admin, attachment_world, attachment_pdf, title="bad\x00text")
    else:
        row = await created_attachment(client_a_admin, attachment_world, attachment_pdf)
        result = await client_a_admin.post(f"/api/attachments/{row['id']}/archive", json={"reason": "bad\x00text"})
    assert result.status_code == 422, result.text


async def test_attachment_foreign_property_is_hidden_before_unit_coherence(
        client_a_admin, attachment_world, attachment_store, attachment_pdf):
    response = await upload(client_a_admin, attachment_world, attachment_pdf,
        property_id=attachment_world["foreign_property"], unit_id=attachment_world["unit"])
    assert response.status_code == 404, response.text


async def test_attachment_role_guards_and_clerk_archive(client_a_admin, client_a_clerk,
        client_a_readonly, client_unauth, attachment_world, attachment_store, attachment_pdf):
    row = await created_attachment(client_a_admin, attachment_world, attachment_pdf)
    path = f"/api/attachments/{row['id']}"
    assert (await client_a_readonly.get(path)).status_code == 200
    assert (await client_unauth.get(path + '/download')).status_code == 401
    assert (await client_a_readonly.post(path + '/archive', json={'reason': 'denied'})).status_code == 403
    result = await client_a_clerk.post(path + '/archive', json={'reason': 'Reviewed by clerk'})
    assert result.status_code == 200, result.text
    assert result.json()['uploaded_by'] == row['uploaded_by']
    assert result.json()['archived_by'] != row['uploaded_by']


async def test_attachment_optional_person_and_contract_are_coherent(client_a_admin,
        attachment_world, attachment_store, attachment_pdf):
    person = await client_a_admin.post('/api/persons', json={'first_name': 'T36', 'last_name': 'Document contact'})
    assert person.status_code == 201, person.text
    tenant = await client_a_admin.post('/api/tenant-profiles', json={'person_id': person.json()['id']})
    assert tenant.status_code == 201, tenant.text
    lease = await client_a_admin.post('/api/leases', json={
        'unit_id': attachment_world['unit'], 'tenant_profile_id': tenant.json()['id'],
        'valid_from': '2026-01-01', 'base_rent': '700.00', 'advance_payment': '150.00'})
    assert lease.status_code == 201, lease.text
    row = await created_attachment(client_a_admin, attachment_world, attachment_pdf,
        person_id=person.json()['id'], lease_agreement_id=lease.json()['id'], unit_id=attachment_world['unit'])
    assert row['person_id'] == person.json()['id'] and row['lease_agreement_id'] == lease.json()['id']
    assert (await client_a_admin.get(f"/api/attachments/{row['id']}/download")).content == attachment_pdf
    selected = await client_a_admin.get('/api/attachments', params={'person_id': person.json()['id']})
    assert [r['id'] for r in selected.json()['items']] == [row['id']]
    assert (await upload(client_a_admin, attachment_world, attachment_pdf,
        property_id=attachment_world['other_property'], lease_agreement_id=lease.json()['id'])).status_code == 422


async def test_attachment_sql_originals_and_parent_coherence(client_a_admin, attachment_world,
        attachment_store, attachment_pdf, migrator_session):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    row = await created_attachment(client_a_admin, attachment_world, attachment_pdf)
    for statement in ("UPDATE attachment SET title='replacement' WHERE id=:id",
                      "UPDATE attachment SET storage_uri='../../outside' WHERE id=:id",
                      "DELETE FROM attachment WHERE id=:id"):
        with pytest.raises(DBAPIError, match='Attachment original'):
            async with migrator_session.begin_nested():
                await migrator_session.execute(text(statement), {'id': row['id']})
    with pytest.raises(DBAPIError, match='parent coherence'):
        async with migrator_session.begin_nested():
            await migrator_session.execute(text('''INSERT INTO attachment(
                organization_id,property_id,unit_id,title,original_filename,mime_type,
                content_byte_size,content_sha256,storage_backend,storage_uri,uploaded_by)
                SELECT organization_id,property_id,:unit,title,original_filename,mime_type,
                content_byte_size,content_sha256,storage_backend,storage_uri,uploaded_by
                FROM attachment WHERE id=:id'''), {'id': row['id'], 'unit': attachment_world['foreign_unit']})
    archived = await client_a_admin.post(f"/api/attachments/{row['id']}/archive", json={'reason': 'Final archive'})
    assert archived.status_code == 200, archived.text
    with pytest.raises(DBAPIError, match='archive is final'):
        async with migrator_session.begin_nested():
            await migrator_session.execute(text('UPDATE attachment SET archived_at=NULL,archived_by=NULL,archive_reason=NULL WHERE id=:id'), {'id': row['id']})


async def test_attachment_sql_archive_requires_nonnull_reason(
        client_a_admin, attachment_world, attachment_store, attachment_pdf,
        migrator_session):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    row = await created_attachment(client_a_admin, attachment_world, attachment_pdf)
    with pytest.raises(DBAPIError, match='ck_attachment_archive'):
        async with migrator_session.begin_nested():
            await migrator_session.execute(text('''
                UPDATE attachment SET archived_at=now(), archived_by=uploaded_by,
                    archive_reason=NULL WHERE id=:id
            '''), {'id': row['id']})


async def test_attachment_commit_visible_at_http_start(client_a_admin, attachment_world,
        attachment_store, attachment_pdf):
    import httpx
    import psycopg2
    from contextlib import closing
    from app.main import app
    from tests.conftest import MIGRATOR_SYNC_URL
    seen = []
    async def observe(scope, receive, send):
        async def observed_send(message):
            if message['type'] == 'http.response.start':
                with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection.cursor() as cursor:
                    cursor.execute('SELECT count(*) FROM attachment WHERE property_id=%s', (attachment_world['property'],))
                    seen.append(cursor.fetchone()[0])
            await send(message)
        await app(scope, receive, observed_send)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observe), base_url='http://test', cookies=client_a_admin.cookies) as api:
        response = await upload(api, attachment_world, attachment_pdf)
    assert response.status_code == 201, response.text
    assert seen == [1], 'Metadata must commit before success is emitted'


@pytest.mark.parametrize('preexisting', [False, True])
@pytest.mark.parametrize('mode', ['hosted', 'selfhost'])
async def test_attachment_failed_audit_rolls_back_metadata_and_obeys_mode_cleanup(
        client_a_admin, attachment_world, attachment_store, attachment_pdf, monkeypatch, seed_data, preexisting, mode):
    import psycopg2
    from contextlib import closing
    from tests.conftest import MIGRATOR_SYNC_URL
    from app.repositories.audit_repo import AuditRepository
    from app.core.config import settings
    monkeypatch.setattr(settings, 'deployment_mode', mode)
    key = make_storage_key(str(seed_data['org_a_id']), sha256(attachment_pdf).hexdigest())
    if preexisting:
        attachment_store.put(key, attachment_pdf, content_type='application/pdf')
    original = AuditRepository.create
    async def fail_after_audit(self, event):
        result = await original(self, event)
        if event.entity_type == 'attachment':
            raise RuntimeError('after attachment audit insert')
        return result
    monkeypatch.setattr(AuditRepository, 'create', fail_after_audit)
    with pytest.raises(RuntimeError, match='after attachment audit insert'):
        await upload(client_a_admin, attachment_world, attachment_pdf)
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT count(*) FROM attachment WHERE property_id=%s', (attachment_world['property'],))
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT count(*) FROM audit_event WHERE entity_type='attachment' AND payload_diff->>'property_id'=%s", (attachment_world['property'],))
        assert cursor.fetchone()[0] == 0
    retained = preexisting or mode == 'selfhost'
    assert attachment_store.exists(key) == retained
    if retained:
        assert attachment_store.get(key) == attachment_pdf


async def test_attachment_storage_failure_is_explicit_without_metadata(client_a_admin,
        attachment_world, attachment_store, attachment_pdf, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('sensitive storage path')
    monkeypatch.setattr(attachment_store, 'put', fail)
    response = await upload(client_a_admin, attachment_world, attachment_pdf)
    assert response.status_code == 503
    assert 'sensitive' not in response.text
    assert (await client_a_admin.get('/api/attachments', params={'property_id': attachment_world['property']})).json()['items'] == []


async def test_attachment_parallel_archive_is_one_transition(client_a_admin, attachment_world,
        attachment_store, attachment_pdf):
    import asyncio
    row = await created_attachment(client_a_admin, attachment_world, attachment_pdf)
    path = f"/api/attachments/{row['id']}/archive"
    first, second = await asyncio.gather(*[
        client_a_admin.post(path, json={'reason': 'Single retained transition'}) for _ in range(2)])
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


async def test_attachment_downgrade_refuses_loss_of_retained_metadata(client_a_admin,
        attachment_world, attachment_store, attachment_pdf):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    row = await created_attachment(client_a_admin, attachment_world, attachment_pdf)
    migration = importlib.import_module('migrations.versions.0130_attachment')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match='retained attachments exist'):
                with connection.begin_nested():
                    migration.downgrade()
            assert connection.scalar(text('SELECT count(*) FROM attachment WHERE id=:id'), {'id': row['id']}) == 1
    finally:
        engine.dispose()
    assert (await client_a_admin.get(f"/api/attachments/{row['id']}/download")).content == attachment_pdf


async def test_attachment_raw_request_limit_applies_before_multipart(client_a_admin):
    from app.core.uploads import MAX_UPLOAD_BYTES, RequestBodyLimitMiddleware
    assert RequestBodyLimitMiddleware._is_upload_scope({'type': 'http', 'method': 'POST', 'path': '/api/attachments'})
    response = await client_a_admin.post('/api/attachments', content=b'small',
        headers={'Content-Type': 'multipart/form-data; boundary=T36', 'Content-Length': str(MAX_UPLOAD_BYTES + 2 * 1024 * 1024)})
    assert response.status_code == 413


async def test_attachment_invalid_storage_reference_never_reaches_store(monkeypatch):
    import uuid
    from types import SimpleNamespace
    from fastapi import HTTPException
    item = SimpleNamespace(organization_id=uuid.uuid4(), content_sha256='a' * 64,
                           storage_uri='../../outside', content_byte_size=1)
    def denied_store():
        pytest.fail('Invalid storage reference reached storage')
    monkeypatch.setattr(attachment_service, 'get_store', denied_store)
    with pytest.raises(HTTPException) as caught:
        attachment_service.AttachmentService._read_verified(item)
    assert caught.value.status_code == 409
