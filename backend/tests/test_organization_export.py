"""Portable organization exports through the authenticated, RLS-bound API."""
import hashlib
import io
import json
import tarfile
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
import psycopg2

from app.core.storage import LocalFsDocumentStore, get_store, set_store
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from tests.conftest import MIGRATOR_SYNC_URL, clear_disposable_source_context
from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401


@pytest_asyncio.fixture
async def export_store(tmp_path, rent_world, rent_db):
    previous = get_store()
    store = LocalFsDocumentStore(tmp_path / "documents")
    set_store(store)
    try:
        yield store
    finally:
        set_store(previous)
        # A failed test body may leave rent_db's transaction open; end it before
        # the synchronous TRUNCATE below, which would otherwise wait on it.
        await rent_db.rollback()
        # Export fixtures retain originals and import receipts. Remove synthetic
        # state through the disposable-DB guard, leaving downgrade guards intact.
        with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection.cursor() as cursor:
            with connection:
                clear_disposable_source_context(cursor)


def unpack(content):
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        return {member.name: archive.extractfile(member).read() for member in archive}


async def test_admin_exports_visible_rows_without_credentials_or_foreign_data(rent_world, export_store):
    w = rent_world
    async with api_client(w) as api:
        response = await api.post("/api/organizations/export")
        assert response.status_code == 200, response.text
        files = unpack(response.content)
        manifest = json.loads(files["manifest.json"])
        assert manifest["format"] == "verwalino-organization-export"
        assert manifest["version"] == 2
        assert manifest["organization"]["id"] == str(w["org"])
        assert manifest["row_counts"]["property"] == 2
        for name, digest in manifest["sha256"].items():
            assert hashlib.sha256(files[name]).hexdigest() == digest
        assert "README.md" in files
        people = [json.loads(line) for line in files["entities/person.jsonl"].splitlines()]
        assert {p["id"] for p in people} == {str(w["person"]), str(w["hidden_person"])}
        users = [json.loads(line) for line in files["entities/user.jsonl"].splitlines()]
        assert len(users) == 3 and all("password_hash" not in user for user in users)
        assert str(w["foreign_org"]).encode() not in b"".join(files.values())
        assert b"unused-synthetic-password" not in b"".join(files.values())
        downloaded = await api.get(f"/api/organizations/export/{response.headers['X-Export-ID']}")
        assert downloaded.status_code == 200 and downloaded.content == response.content
    async with api_client(w, foreign=True) as foreign:
        assert (await foreign.get(f"/api/organizations/export/{response.headers['X-Export-ID']}")).status_code == 404


@pytest.mark.parametrize("actor", ["reader", "clerk"])
async def test_export_requires_an_owner_administrator(rent_world, actor, export_store):
    async with api_client(rent_world, actor) as api:
        assert (await api.post("/api/organizations/export")).status_code == 403


async def test_export_keeps_original_bytes_exact_decimals_and_existing_csv(rent_world, rent_db, export_store):
    from tests.test_letters import create_template, issue_body
    w = rent_world
    await rent_db.execute(text("UPDATE unit SET mea=1234567890123.123456 WHERE id=:id"), {"id": w["unit"]})
    await rent_db.execute(text("""INSERT INTO audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES (:org,:actor,'organization',:org,'precision.fixture',CAST(:payload AS jsonb))"""),
        {"org": w["org"], "actor": w["admin"], "payload":
         '{"exact":1234567890123.123456,"password_hash":"do-not-export-this","nested":{"lease_token":"private-lease"}}'})
    await rent_db.commit()
    async with api_client(w) as api:
        template = await create_template(api)
        issued = await api.post("/api/letters/issue", json=issue_body(w, template))
        assert issued.status_code == 201, issued.text
        original = await api.get(issued.json()[0]["pdf_url"])
        response = await api.post("/api/organizations/export")
        assert response.status_code == 200, response.text
        files = unpack(response.content)
        assert files[f"documents/{issued.json()[0]['id']}"] == original.content
        units = [json.loads(line) for line in files["entities/unit.jsonl"].splitlines()]
        assert next(row for row in units if row["id"] == str(w["unit"]))["mea"] == "1234567890123.123456"
        events = [json.loads(line, parse_float=Decimal) for line in files["entities/audit_event.jsonl"].splitlines()]
        precision = next(e for e in events if e["action"] == "precision.fixture")
        assert precision["payload_diff"]["exact"] == Decimal("1234567890123.123456")
        assert b"do-not-export-this" not in b"".join(files.values())
        assert b"private-lease" not in b"".join(files.values())
        downloaded = await api.get(f"/api/organizations/export/{response.headers['X-Export-ID']}")
        assert downloaded.status_code == 200 and downloaded.content == response.content
        for entity, path in (("payment", "payments"), ("cost_entry", "cost-entries")):
            csv = await api.get(f"/api/{path}/export.csv")
            assert files[f"csv/{entity}.csv"] == csv.content


async def test_export_refuses_a_corrupt_original_without_an_artifact(rent_world, export_store):
    from tests.test_letters import create_template, issue_body
    from app.services.organization_export_service import export_store as artifacts
    async with api_client(rent_world) as api:
        template = await create_template(api)
        issued = await api.post("/api/letters/issue", json=issue_body(rent_world, template))
        assert issued.status_code == 201, issued.text
        original = next((export_store._root / str(rent_world["org"])).rglob("*.bin"))
        original.chmod(0o600)
        original.write_bytes(b"corrupted")
        response = await api.post("/api/organizations/export")
        assert response.status_code == 409, response.text
        assert not list(artifacts()._root.rglob("*.tar.gz"))
        assert not list(artifacts()._root.rglob("*.pending"))


async def test_export_audit_commits_before_any_bytes_are_disclosed(rent_world, export_store):
    from app.services.organization_export_service import export_store as artifacts
    def fail_commit(session):
        if session.info.get("export_response_streams"):
            raise RuntimeError("synthetic export audit commit failure")
    event.listen(Session, "before_commit", fail_commit)
    try:
        async with api_client(rent_world) as api:
            with pytest.raises(RuntimeError, match="synthetic export audit commit failure"):
                await api.post("/api/organizations/export")
        assert not list(artifacts()._root.rglob("*.tar.gz"))
    finally:
        event.remove(Session, "before_commit", fail_commit)


async def test_expired_exports_are_removed_without_touching_originals(rent_world, export_store):
    from app.services.organization_export_service import cleanup_expired_exports, export_store as artifacts
    async with api_client(rent_world) as api:
        response = await api.post("/api/organizations/export")
        assert response.status_code == 200, response.text
        path = next(artifacts()._root.rglob("*.tar.gz"))
        past = (datetime.now(timezone.utc) - timedelta(hours=25)).timestamp()
        os.utime(path, (past, past))
        original = export_store.put(f"{rent_world['org']}/unrelated.bin", b"retained", content_type="text/plain")
        assert cleanup_expired_exports() == 1
        assert export_store.get(original.uri) == b"retained"
        assert (await api.get(f"/api/organizations/export/{response.headers['X-Export-ID']}")).status_code == 404


async def test_exports_are_rate_limited_and_replacements_remove_old_bytes(rent_world, export_store, monkeypatch):
    from app.services import organization_export_service as service
    async with api_client(rent_world) as api:
        first = await api.post("/api/organizations/export")
        assert first.status_code == 200, first.text
        refusal = await api.post("/api/organizations/export")
        assert refusal.status_code == 429 and refusal.headers["Retry-After"] == "60"
        monkeypatch.setattr(service, "EXPORT_INTERVAL", timedelta(0))
        second = await api.post("/api/organizations/export")
        assert second.status_code == 200, second.text
        assert len(list(service.export_store()._root.rglob("*.tar.gz"))) == 1
        assert (await api.get(f"/api/organizations/export/{first.headers['X-Export-ID']}")).status_code == 404


async def test_export_handles_many_rows_and_long_unicode_text(rent_world, rent_db, export_store):
    await rent_db.execute(text("""INSERT INTO person(organization_id,first_name,last_name,street,city)
        SELECT :org,'Änne',repeat('Große Wohnungsverwaltung ',10),repeat('Lange Straße ',15),'München'
        FROM generate_series(1,1000)"""), {"org": rent_world["org"]})
    await rent_db.commit()
    async with api_client(rent_world) as api:
        response = await api.post("/api/organizations/export")
        assert response.status_code == 200, response.text
        files = unpack(response.content)
        assert json.loads(files["manifest.json"])["row_counts"]["person"] == 1002
        people = [json.loads(line) for line in files["entities/person.jsonl"].splitlines()]
        assert len([p for p in people if p["first_name"] == "Änne"]) == 1000


async def test_export_omits_read_only_sources_and_rechecks_download_authority(rent_world, rent_db, export_store):
    from tests.test_property_source_enforcement import synthetic_enforcement
    from tests.test_property_source_access import grant
    from tests.test_person_source_access import audience
    w = rent_world
    seed = {"org_a_id": w["org"], "user_a_admin_id": w["admin"]}
    await rent_db.execute(text("""INSERT INTO audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
        VALUES (:org,:actor,'person',:person,'person.fixture',CAST(:payload AS jsonb))"""),
        {"org": w["org"], "actor": w["admin"], "person": w["person"],
         "payload": '{"old":{"email":"private-audit-copy@example.invalid"}}'})
    await rent_db.execute(text("""INSERT INTO master_import_batch(organization_id,actor_user_id,request_key,request_digest,result)
        VALUES (:org,:actor,:request,:digest,CAST(:result AS jsonb))"""),
        {"org": w["org"], "actor": w["admin"], "request": __import__('uuid').uuid4(), "digest": "f" * 64,
         "result": json.dumps({"rows": [{"kind": "person", "key": "private-import-key", "id": str(w["person"])}], "counts": {"persons": 1}})})
    await rent_db.commit()
    async with api_client(w) as api, synthetic_enforcement(seed):
        domain, permission = await grant(api, seed, w["property"])
        await audience(api, str(w["person"]), w["admin"], ("read",))
        assert (await api.get(f"/api/persons/{w['person']}")).status_code == 200
        response = await api.post("/api/organizations/export")
        assert response.status_code == 200, response.text
        files = unpack(response.content)
        assert files["entities/person.jsonl"] == b""
        assert files["entities/tenant_profile.jsonl"] == b""
        assert files["entities/lease_agreement.jsonl"] == b""
        assert b"private-audit-copy" not in b"".join(files.values())
        assert b"private-import-key" not in b"".join(files.values())
        assert files["entities/master_import_batch.jsonl"] == b""
        downloaded = await api.get(f"/api/organizations/export/{response.headers['X-Export-ID']}")
        assert downloaded.status_code == 200 and downloaded.content == response.content
        limited = await api.post("/api/organizations/export")
        assert limited.status_code == 429
        # Revoking an exported property also invalidates the existing download.
        revoked = await api.post(f"/api/property-data-domains/{domain}/grants/{permission}/revoke")
        assert revoked.status_code == 204, revoked.text
        assert (await api.get(f"/api/organizations/export/{response.headers['X-Export-ID']}")).status_code == 404


def test_source_identity_uses_latest_local_receipt_and_ignores_imported_history():
    from app.services.organization_export_service import source_identity_maps
    def receipt(identity, created, source, *, imported=False):
        return {'id': identity, 'occurred_at': created, 'action': 'organization.import.completed',
                'payload_diff': {'source_ids': {'organization': {'target': source}}, 'imported_receipt': imported}}
    snapshot = {'audit_event': [
        receipt('a', '2026-10-31T12:00:00+00:00', 'latest'),
        receipt('z', '2026-10-30T12:00:00+00:00', 'older'),
        receipt('future', '2030-01-01T12:00:00+00:00', 'imported', imported=True),
    ]}
    assert source_identity_maps(snapshot)['organization'] == {'target': 'latest'}
