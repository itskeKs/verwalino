"""T48: saved plain-text templates and immutable, source-authorized letters."""
import asyncio
import io
import uuid

import pdfplumber
import pytest
from sqlalchemy import text

from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_scoped_account_statements import mutate

pytestmark = pytest.mark.asyncio

TEMPLATE = {"name": "Hausinformation", "subject": "Information {{objekt.adresse}}", "body": "Guten Tag {{mieter.name}},\nTermin am {{datum}}. <img src=\"http://forbidden.invalid\">"}


async def create_template(api, **changes):
    response = await api.post("/api/letters/templates", json={**TEMPLATE, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def issue_body(world, template, **changes):
    return {"template_id": template["id"], "property_id": str(world["property"]),
            "as_of": "2026-06-01", "request_id": str(uuid.uuid4()), **changes}


def pdf_text(content):
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


async def test_letters_template_persistence_and_immutable_personalized_pdf(rent_world):
    w = rent_world
    async with api_client(w) as api:
        template = await create_template(api)
        assert template in (await api.get("/api/letters/templates")).json()
        body = issue_body(w, template)
        issued = await api.post("/api/letters/issue", json=body)
        assert issued.status_code == 201, issued.text
        rows = issued.json()
        assert len(rows) == 1
        assert rows[0]["person_id"] == str(w["person"])
        pdf = await api.get(rows[0]["pdf_url"])
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
        content = pdf_text(pdf.content)
        assert "Synthetic T23 tenant" in content
        assert "01.06.2026" in content
        assert '<img src="http://forbidden.invalid">' in content
        assert "{{" not in content
        changed = await api.put(f"/api/letters/templates/{template['id']}", json={**TEMPLATE, "body": "Geänderte Vorlage"})
        assert changed.status_code == 200, changed.text
        await mutate(text("UPDATE person SET first_name='Changed' WHERE id=:id"), {"id": w["person"]})
        assert (await api.get(rows[0]["pdf_url"])).content == pdf.content
        assert (await api.post("/api/letters/issue", json=body)).json() == rows
        listed = await api.get("/api/letters/documents", params={"property_id": str(w["property"])})
        assert listed.json() == rows
        conflict = await api.post("/api/letters/issue", json={**body, "as_of": "2026-07-01"})
        assert conflict.status_code == 409


@pytest.mark.parametrize("body", ["{{unknown}}", "{{mieter.__class__}}", "{{ mieter.name }", "{% for x in y %}", "{{objekt.adresse}} {{mieter.name()}}"])
async def test_letters_reject_unsupported_template_syntax(rent_world, body):
    async with api_client(rent_world) as api:
        response = await api.post("/api/letters/templates", json={**TEMPLATE, "body": body})
        assert response.status_code == 422, response.text
        assert (await api.get("/api/letters/templates")).json() == []


async def test_letters_roles_foreign_ids_and_live_restriction(rent_world):
    w = rent_world
    async with api_client(w) as api, api_client(w, "reader") as reader, api_client(w, foreign=True) as foreign:
        template = await create_template(api)
        body = issue_body(w, template)
        assert (await reader.post("/api/letters/templates", json=TEMPLATE)).status_code == 403
        assert (await reader.post("/api/letters/issue", json=body)).status_code == 403
        assert (await foreign.get("/api/letters/templates")).json() == []
        assert (await foreign.post("/api/letters/issue", json=body)).status_code == 404
        issued = await api.post("/api/letters/issue", json=body)
        assert issued.status_code == 201, issued.text
        url = issued.json()[0]["pdf_url"]
        assert (await foreign.get(url)).status_code == 404
        await mutate(text("UPDATE person SET processing_restricted=true WHERE id=:id"), {"id": w["person"]})
        assert (await api.get(url)).status_code == 409
        assert (await api.post("/api/letters/issue", json=issue_body(w, template))).status_code == 409
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE user_id=:id"), {"id": w["admin"]})
        assert (await api.post("/api/letters/issue", json=issue_body(w, template))).status_code == 403


async def test_letters_parallel_replay_and_empty_period(rent_world):
    w = rent_world
    async with api_client(w) as api:
        template = await create_template(api)
        empty = await api.post("/api/letters/issue", json=issue_body(w, template, as_of="2025-01-01"))
        assert empty.status_code == 422
        body = issue_body(w, template)
        async def send():
            async with api_client(w) as other:
                return await other.post("/api/letters/issue", json=body)
        first, second = await asyncio.gather(send(), send())
        assert first.status_code == second.status_code == 201
        assert first.json() == second.json()
        assert len((await api.get("/api/letters/documents", params={"property_id": str(w["property"])})).json()) == 1


async def add_recipient(w):
    ids = {"org": w["org"], "property": w["property"], "person": uuid.uuid4(),
           "tenant": uuid.uuid4(), "unit": uuid.uuid4(), "lease": uuid.uuid4()}
    await mutate(text("INSERT INTO person(id,organization_id,first_name,last_name) VALUES (:person,:org,'Berta','Rundbrief')"), ids)
    await mutate(text("INSERT INTO tenant_profile(id,organization_id,person_id) VALUES (:tenant,:org,:person)"), ids)
    await mutate(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit,:org,:property,'2','WOHNUNG')"), ids)
    await mutate(text("""INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,valid_to,base_rent,advance_payment)
        VALUES (:lease,:org,:unit,:tenant,'2026-01-01','2026-07-01',600,100)"""), ids)
    return ids


async def test_letters_all_current_people_deduplicated_and_exclusive_end(rent_world):
    w = rent_world
    second = await add_recipient(w)
    duplicate = {**second, "unit": uuid.uuid4(), "lease": uuid.uuid4(), "tenant": w["tenant"]}
    await mutate(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit,:org,:property,'3','WOHNUNG')"), duplicate)
    await mutate(text("""INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment)
        VALUES (:lease,:org,:unit,:tenant,'2026-01-01',10,0)"""), duplicate)
    async with api_client(w) as api:
        template = await create_template(api)
        issued = await api.post("/api/letters/issue", json=issue_body(w, template))
        assert issued.status_code == 201, issued.text
        assert {row["person_id"] for row in issued.json()} == {str(w["person"]), str(second["person"])}
        for row in issued.json():
            content = pdf_text((await api.get(row["pdf_url"])).content)
            assert row["recipient_name"] in content
            assert ("Berta Rundbrief" in content) == (row["person_id"] == str(second["person"]))
        july = await api.post("/api/letters/issue", json=issue_body(w, template, as_of="2026-07-01"))
        assert july.status_code == 201, july.text
        assert [row["person_id"] for row in july.json()] == [str(w["person"])]


async def test_letters_hidden_contact_blocks_whole_batch_and_replay(rent_world):
    from tests.test_property_source_enforcement import synthetic_enforcement
    from tests.test_property_source_access import grant
    from tests.test_person_source_access import audience
    w = rent_world
    second = await add_recipient(w)
    seed = {"org_a_id": w["org"], "user_a_admin_id": w["admin"]}
    async with api_client(w) as api:
        template = await create_template(api)
        async with synthetic_enforcement(seed):
            await grant(api, seed, w["property"])
            await audience(api, str(w["person"]), w["admin"], ('read','prepare','export'))
            hidden = await api.post("/api/letters/issue", json=issue_body(w, template))
            assert hidden.status_code == 404, hidden.text
            assert (await api.get("/api/letters/documents", params={"property_id": str(w["property"])})).json() == []
            domain, permission = await audience(api, str(second["person"]), w["admin"], ('read','prepare','export'))
            body = issue_body(w, template)
            issued = await api.post("/api/letters/issue", json=body)
            assert issued.status_code == 201, issued.text
            assert len(issued.json()) == 2
            revoke = await api.post(f"/api/person-data-domains/{domain}/grants/{permission}/revoke")
            assert revoke.status_code == 204, revoke.text
            replay = await api.post("/api/letters/issue", json=body)
            assert replay.status_code == 404, replay.text


async def test_letters_database_original_is_immutable(rent_world):
    from app.core.db import get_tenant_session
    from sqlalchemy.exc import DBAPIError
    w = rent_world
    async with api_client(w) as api:
        template = await create_template(api)
        result = await api.post("/api/letters/issue", json=issue_body(w, template))
        assert result.status_code == 201, result.text
    with pytest.raises(DBAPIError):
        await mutate(text("UPDATE letter_document SET body='tampered' WHERE organization_id=:org"), w)
    async with get_tenant_session(w["foreign_org"]) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(w["foreign_admin"])})
        assert await db.scalar(text("SELECT count(*) FROM letter_template WHERE organization_id=:org"), w) == 0
        assert await db.scalar(text("SELECT count(*) FROM letter_document WHERE organization_id=:org"), w) == 0


async def test_letters_failed_second_render_rolls_back_entire_batch(rent_world, monkeypatch):
    from app.services import letter_service
    w = rent_world
    await add_recipient(w)
    renderer = letter_service.render_letter
    calls = 0
    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('Synthetic renderer failure')
        return renderer(*args)
    monkeypatch.setattr(letter_service, 'render_letter', fail_second)
    async with api_client(w) as api:
        template = await create_template(api)
        with pytest.raises(RuntimeError, match='Synthetic renderer failure'):
            await api.post('/api/letters/issue', json=issue_body(w, template))
        assert calls == 2
        assert (await api.get('/api/letters/documents', params={'property_id': str(w['property'])})).json() == []


async def test_letters_migration_roundtrip_preserves_existing_data_and_security(rent_world):
    """Exercise the actual migration operations; rollback restores synthetic letters too."""
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL

    async with api_client(rent_world) as api:
        template = await create_template(api)
        issued = await api.post("/api/letters/issue", json=issue_body(rent_world, template))
        assert issued.status_code == 201, issued.text
        original_pdf = (await api.get(issued.json()[0]["pdf_url"])).content
    path = Path(__file__).parents[1] / "migrations/versions/0132_letter_template.py"
    spec = importlib.util.spec_from_file_location("letters_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            def snapshot():
                tables = connection.execute(text("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname NOT IN ('letter_document','letter_template')
                    AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')
                    ORDER BY c.relname""")).scalars().all()
                rows = {table: connection.execute(text(f'SELECT to_jsonb(t)::text FROM "{table}" t ORDER BY to_jsonb(t)::text')).scalars().all() for table in tables}
                security = [connection.execute(text(query)).all() for query in (
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname NOT LIKE 'letter_%' ORDER BY c.relname",
                    "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' AND tablename NOT LIKE 'letter_%' ORDER BY tablename,policyname",
                    "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND c.relname NOT LIKE 'letter_%' ORDER BY pg_get_triggerdef(t.oid)",
                    "SELECT pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND p.proname NOT LIKE 'letter_%' ORDER BY p.proname,p.oid",
                )]
                return rows, security
            try:
                before = snapshot()
                assert before[0]['lease_agreement'], 'Must preserve actual existing leases'
                with Operations.context(MigrationContext.configure(connection)):
                    with pytest.raises(DBAPIError, match='Cannot remove populated letter tables'):
                        with connection.begin_nested():
                            migration.downgrade()
                    assert snapshot() == before
                    connection.execute(text('TRUNCATE letter_document,letter_template'))
                    migration.downgrade()
                    assert connection.scalar(text("SELECT to_regclass('public.letter_template')")) is None
                    assert snapshot() == before
                    migration.upgrade()
                    assert snapshot() == before
                    assert connection.scalar(text("SELECT count(*) FROM pg_policies WHERE tablename IN ('letter_template','letter_document')")) == 5
                    assert connection.scalar(text("SELECT count(*) FROM pg_class WHERE relname IN ('letter_template','letter_document') AND relrowsecurity AND relforcerowsecurity")) == 2
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    async with api_client(rent_world) as api:
        assert (await api.get(issued.json()[0]["pdf_url"])).content == original_pdf
