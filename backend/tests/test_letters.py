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


async def lease_landlord(api, world):
    person = await api.post('/api/persons', json={
        'first_name': 'Frida', 'last_name': 'Sonnenhof', 'street': 'Sonnenweg 8',
        'zip_code': '12345', 'city': 'Teststadt',
    })
    assert person.status_code == 201, person.text
    owner = await api.post('/api/owner-profiles', json={'person_id': person.json()['id']})
    assert owner.status_code == 201, owner.text
    history = await api.post('/api/ownership', json={
        'unit_id': str(world['unit']), 'owner_profile_id': owner.json()['id'], 'valid_from': '2025-01-01',
    })
    assert history.status_code == 201, history.text
    return person.json()


async def test_letter_placeholders_include_lease_unit_and_original_landlord(rent_world):
    w = rent_world
    async with api_client(w) as api:
        await lease_landlord(api, w)
        template = await create_template(api, name='Vertragsdaten', subject='Einheit {{einheit.nummer}}',
            body='{{vermieter.name}}\n{{vermieter.adresse}}\n{{mieter.name}}\n{{vertrag.beginn}}\n'
                 '{{vertrag.ende}}\n{{vertrag.kaltmiete}}\n{{vertrag.vorauszahlung}}\n{{vertrag.kaution}}')
        issued = await api.post('/api/letters/issue', json=issue_body(w, template))
        assert issued.status_code == 201, issued.text
        original = await api.get(issued.json()[0]['pdf_url'])
        content = pdf_text(original.content)
        for expected in ('Einheit 1', 'Frida Sonnenhof', 'Sonnenweg 8', 'Synthetic T23 tenant',
                         '01.01.2026', 'Unbefristet', '700,00', '150,00', 'Nicht erfasst'):
            assert expected in content
        await mutate(text("UPDATE person SET processing_restricted=true WHERE id IN (SELECT person_id FROM owner_profile WHERE organization_id=:org)"), w)
        assert (await api.get(issued.json()[0]['pdf_url'])).status_code == 409


def contract_body(**changes):
    return {'request_id': str(uuid.uuid4()), 'as_of': '2026-01-01',
            'handover_date': '2026-01-02', 'agreements': 'Freitext: Gartenmitbenutzung prüfen.', **changes}


async def test_lease_contract_uses_template_variables_and_retains_original(rent_world):
    w = rent_world
    async with api_client(w) as api:
        await lease_landlord(api, w)
        body = contract_body()
        issued = await api.post(f"/api/leases/{w['lease']}/contract", json=body)
        assert issued.status_code == 201, issued.text
        row = issued.json()
        pdf = await api.get(row['pdf_url'])
        assert pdf.status_code == 200, pdf.text
        content = pdf_text(pdf.content)
        for expected in ('Mietvertrag', 'rechtliche Prüfung erforderlich', 'Frida Sonnenhof',
                         'Synthetic T23 tenant', 'Einheit 1', '700,00', '150,00',
                         '02.01.2026', 'Gartenmitbenutzung'):
            assert expected in content
        assert (await api.get('/api/letters/templates')).json() == []
        from app.services.lease_document_service import CONTRACT_TEMPLATE
        template = await create_template(api, name='Eigene Vertragsvorlage',
            subject='Mietvertrag · Einheit {{einheit.nummer}}', body=CONTRACT_TEMPLATE)
        circular = await api.post('/api/letters/issue', json=issue_body(w, template, as_of='2026-01-01'))
        assert circular.status_code == 201, circular.text
        circular_text = pdf_text((await api.get(circular.json()[0]['pdf_url'])).content)
        assert 'Vorlage; rechtliche Prüfung erforderlich' in circular_text
        changed = await api.put(f"/api/letters/templates/{template['id']}", json={
            'name': 'Mietvertrag', 'subject': 'Mietvertrag {{einheit.nummer}}',
            'body': 'Neu {{vermieter.name}} {{vertrag.kaltmiete}} {{vertrag.vorauszahlung}}',
        })
        assert changed.status_code == 200, changed.text
        assert (await api.get(row['pdf_url'])).content == pdf.content
        assert (await api.post(f"/api/leases/{w['lease']}/contract", json=body)).json() == row
        conflict = await api.post(f"/api/leases/{w['lease']}/contract", json={**body, 'agreements': 'Changed'})
        assert conflict.status_code == 409, conflict.text
        listing = await api.get(f"/api/leases/{w['lease']}/documents")
        assert listing.status_code == 200 and row in listing.json()


async def test_lease_contract_ignores_a_circular_template_named_mietvertrag(rent_world):
    async with api_client(rent_world) as api:
        template = await create_template(api, name='Mietvertrag', subject='Rundschreiben', body='Termin für die Hausversammlung')
        response = await api.post(f"/api/leases/{rent_world['lease']}/contract", json=contract_body())
        assert response.status_code == 201, response.text
        content = pdf_text((await api.get(response.json()['pdf_url'])).content)
        assert 'Monatliche Kaltmiete (Originalvertrag)' in content
        assert 'Hausversammlung' not in content
        assert (await api.get('/api/letters/templates')).json() == [template]
        assert response.json()['template_id'] is None


async def test_handover_does_not_create_an_unused_letter_template(rent_world):
    async with api_client(rent_world) as api:
        response = await api.post(f"/api/leases/{rent_world['lease']}/handover", json=handover_body())
        assert response.status_code == 201, response.text
        assert (await api.get('/api/letters/templates')).json() == []
        assert response.json()['template_id'] is None


async def test_builtin_original_prevents_a_downgrade_that_requires_templates(rent_world, rent_db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy.exc import IntegrityError

    async with api_client(rent_world) as api:
        response = await api.post(f"/api/leases/{rent_world['lease']}/contract", json=contract_body())
        assert response.status_code == 201, response.text
        original = (await api.get(response.json()['pdf_url'])).content

        def downgrade(connection):
            with Operations.context(MigrationContext.configure(connection)):
                ScriptDirectory('migrations').get_revision('0143').module.downgrade()

        with pytest.raises(IntegrityError):
            async with rent_db.begin_nested():
                await (await rent_db.connection()).run_sync(downgrade)
        assert (await api.get(response.json()['pdf_url'])).content == original


async def test_lease_contract_refuses_foreign_lease_and_readers(rent_world):
    async with api_client(rent_world) as api, api_client(rent_world, 'reader') as reader:
        for lease in (rent_world['foreign_lease'], uuid.uuid4()):
            response = await api.post(f'/api/leases/{lease}/contract', json=contract_body())
            assert response.status_code == 404, response.text
        response = await reader.post(f"/api/leases/{rent_world['lease']}/contract", json=contract_body())
        assert response.status_code == 403, response.text


@pytest.mark.parametrize('body', [
    '{"document_type":"letter","landlord_sources":"plain letter text"}',
    '{"format":"verwalino.letter.v1","document_type":"letter","text":"JSON example","landlord_sources":["example"]}',
])
async def test_plain_json_letter_text_is_not_treated_as_authorization_metadata(rent_world, body):
    async with api_client(rent_world) as api:
        template = await create_template(api, body=body)
        issued = await api.post('/api/letters/issue', json=issue_body(rent_world, template))
        assert issued.status_code == 201, issued.text
        pdf = await api.get(issued.json()[0]['pdf_url'])
        assert pdf.status_code == 200, pdf.text
        assert ''.join(body.split()) in ''.join(pdf_text(pdf.content).split())


def handover_body(**changes):
    return {'request_id': str(uuid.uuid4()), 'date': '2026-01-02', 'type': 'Einzug',
            'parties': 'Frida Sonnenhof; Synthetic T23 tenant', 'readings': [],
            'keys': [{'type': 'Haustür', 'count': 3}], 'notes': 'Kratzer am Fenster <img src="x">',
            'signatures': 'Frida Sonnenhof; Synthetic T23 tenant', **changes}


async def unit_meter(api, w, number='W37'):
    meter = await api.post('/api/meters', json={'property_id': str(w['property']), 'unit_id': str(w['unit']),
        'meter_number': number, 'meter_type': 'KALTWASSER'})
    assert meter.status_code == 201, meter.text
    row = meter.json()
    response = await api.post(f"/api/meters/{row['id']}/readings", json={'reading_date': '2026-01-01', 'value': '100'})
    assert response.status_code == 201, response.text
    return row


async def test_handover_records_exact_readings_and_replays_without_duplicates(rent_world, rent_db):
    w = rent_world
    async with api_client(w) as api:
        meter = await unit_meter(api, w)
        body = handover_body(readings=[{'meter_id': meter['id'], 'value': '1234.5678'}])
        url = f"/api/leases/{w['lease']}/handover"
        response = await api.post(url, json=body)
        assert response.status_code == 201, response.text
        row = response.json()
        original = await api.get(row['pdf_url'])
        content = pdf_text(original.content)
        for expected in ('Übergabeprotokoll', 'Einzug', '02.01.2026', 'W37', '1.234,5678', 'Haustür', '3', 'Kratzer am Fenster', 'Frida Sonnenhof'):
            assert expected in content
        assert (await api.post(url, json=body)).json() == row
        readings = (await api.get(f"/api/meters/{meter['id']}/readings")).json()
        assert len(readings) == 2
        assert readings[-1]['value'] == '1234.5678'
        assert readings[-1]['period'] == 'Übergabeprotokoll 2026-01-02'
        saved = (await rent_db.execute(text('SELECT body FROM letter_document WHERE id=:id'), {'id': row['id']})).scalar_one()
        import json
        payload = json.loads(saved)
        assert payload['input'] == body
        assert payload['meters'][0]['reading_id'] == readings[-1]['id']
        await mutate(text("UPDATE meter SET meter_number='CHANGED' WHERE id=:id"), {'id': meter['id']})
        assert (await api.get(row['pdf_url'])).content == original.content


async def test_handover_reuses_an_omitted_prefilled_reading_without_a_duplicate(rent_world, rent_db):
    import json
    w = rent_world
    async with api_client(w) as api:
        meter = await unit_meter(api, w)
        original = (await api.get(f"/api/meters/{meter['id']}/readings")).json()
        body = handover_body()
        response = await api.post(f"/api/leases/{w['lease']}/handover", json=body)
        assert response.status_code == 201, response.text
        row = response.json()
        assert (await api.get(f"/api/meters/{meter['id']}/readings")).json() == original
        saved = (await rent_db.execute(text('SELECT body FROM letter_document WHERE id=:id'), {'id': row['id']})).scalar_one()
        assert json.loads(saved)['meters'][0]['reading_id'] == original[0]['id']
        assert '100,0000' in pdf_text((await api.get(row['pdf_url'])).content)
        assert (await api.post(f"/api/leases/{w['lease']}/handover", json=body)).json() == row


async def test_handover_requires_input_when_no_reading_exists_on_or_before_its_date(rent_world):
    w = rent_world
    async with api_client(w) as api:
        await unit_meter(api, w)
        response = await api.post(f"/api/leases/{w['lease']}/handover", json=handover_body(date='2025-12-31'))
        assert response.status_code == 422, response.text
        assert (await api.get(f"/api/leases/{w['lease']}/documents")).json() == []


async def test_handover_lower_reading_rolls_back_every_reading_and_document(rent_world):
    w = rent_world
    async with api_client(w) as api:
        meters = [await unit_meter(api, w, name) for name in ('A', 'B')]
        response = await api.post(f"/api/leases/{w['lease']}/handover", json=handover_body(readings=[
            {'meter_id': meters[0]['id'], 'value': '200'}, {'meter_id': meters[1]['id'], 'value': '99'},
        ]))
        assert response.status_code == 422, response.text
        assert 'Zählerstand' in response.text
        for meter in meters:
            assert len((await api.get(f"/api/meters/{meter['id']}/readings")).json()) == 1
        assert (await api.get(f"/api/leases/{w['lease']}/documents")).json() == []


async def test_handover_without_meters_omits_meter_table_and_works_for_ended_lease(rent_world):
    w = rent_world
    async with api_client(w) as api:
        ended = await api.patch(f"/api/leases/{w['lease']}", json={'valid_to': '2026-01-31'})
        assert ended.status_code == 200, ended.text
        response = await api.post(f"/api/leases/{w['lease']}/handover", json=handover_body(date='2026-02-01', type='Auszug'))
        assert response.status_code == 201, response.text
        content = pdf_text((await api.get(response.json()['pdf_url'])).content)
        assert 'Auszug' in content and '01.02.2026' in content
        assert 'Zählerstände' not in content


async def test_handover_of_a_finalized_lease_preserves_the_settlement(rent_world, rent_db):
    from tests.financial_coverage_helpers import attest_fixture
    w = rent_world
    async with api_client(w) as api:
        response = await api.post('/api/settlements', json={'heating_advance_payments_total': '0.00', 'property_id': str(w['property']),
            'lease_agreement_id': str(w['lease']), 'period_start': '2026-01-01',
            'period_end': '2026-01-31', 'advance_payments_total': '0.00'})
        assert response.status_code == 201, response.text
        run_id = response.json()['id']
        await attest_fixture(api, 'settlement', run_id, {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL', 'advance_payments_total': '0.00', 'advance_payments_source': 'MANUAL'})
        response = await api.post(f'/api/settlements/{run_id}/finalize', json={})
        assert response.status_code == 200, response.text
        finalized = response.json()
        pdf = (await api.get(f'/api/settlements/{run_id}/pdf')).content
        response = await api.post(f"/api/leases/{w['lease']}/handover", json=handover_body(type='Auszug', date='2026-02-01'))
        assert response.status_code == 201, response.text
        assert (await api.get(f'/api/settlements/{run_id}')).json() == finalized
        assert (await api.get(f'/api/settlements/{run_id}/pdf')).content == pdf


async def test_handover_render_failure_rolls_back_readings_and_original(rent_world, monkeypatch):
    from app.services import lease_document_service
    def fail(*args):
        raise RuntimeError('Synthetic protocol render failure')
    w = rent_world
    async with api_client(w) as api:
        meter = await unit_meter(api, w)
        monkeypatch.setattr(lease_document_service, 'render_letter', fail)
        with pytest.raises(RuntimeError, match='Synthetic protocol render failure'):
            await api.post(f"/api/leases/{w['lease']}/handover", json=handover_body(readings=[{'meter_id': meter['id'], 'value': '200'}]))
        assert len((await api.get(f"/api/meters/{meter['id']}/readings")).json()) == 1
        assert (await api.get(f"/api/leases/{w['lease']}/documents")).json() == []


async def test_lease_document_keeps_original_landlord_export_and_processing_checks(rent_world):
    from tests.test_property_source_enforcement import synthetic_enforcement
    from tests.test_property_source_access import grant
    from tests.test_person_source_access import audience
    w = rent_world
    seed = {'org_a_id': w['org'], 'user_a_admin_id': w['admin']}
    async with api_client(w) as api:
        landlord = await lease_landlord(api, w)
        await grant(api, seed, w['property'])
        await audience(api, str(w['person']), w['admin'], ('read', 'prepare', 'export'))
        domain, permission = await audience(api, landlord['id'], w['admin'], ('read', 'prepare', 'export'))
        async with synthetic_enforcement(seed):
            response = await api.post(f"/api/leases/{w['lease']}/contract", json=contract_body())
            assert response.status_code == 201, response.text
            url = response.json()['pdf_url']
            assert (await api.get(url)).status_code == 200
            revoked = await api.post(f'/api/person-data-domains/{domain}/grants/{permission}/revoke')
            assert revoked.status_code == 204, revoked.text
            assert (await api.get(url)).status_code == 404
        await mutate(text('UPDATE person SET processing_restricted=true WHERE id=:id'), {'id': landlord['id']})
        assert (await api.get(url)).status_code == 409


async def test_handover_rejects_duplicate_and_foreign_unit_meters(rent_world):
    w = rent_world
    async with api_client(w) as api, api_client(w, foreign=True) as foreign:
        meter = await unit_meter(api, w)
        foreign_meter = await unit_meter(foreign, {**w, 'unit': w['foreign_unit'], 'property': w['foreign_property']})
        for readings, status in (([{'meter_id': meter['id'], 'value': '100'}] * 2, 422),
                                 ([{'meter_id': foreign_meter['id'], 'value': '100'}], 404)):
            response = await api.post(f"/api/leases/{w['lease']}/handover", json=handover_body(readings=readings))
            assert response.status_code == status, response.text
        assert (await api.get(f"/api/leases/{w['lease']}/documents")).json() == []
