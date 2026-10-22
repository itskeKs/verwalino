"""T56: remove actual unused mistakes, never referenced financial/source history."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from tests.test_rent_changes import api_client, post_change, rent_db, rent_world  # noqa: F401


async def unused(api, kind):
    if kind == 'persons':
        response = await api.post('/api/persons', json={'first_name': 'Synthetic', 'last_name': 'T56 Unused'})
    else:
        response = await api.post('/api/properties', json={'name': 'T56 Unused', 'management_type': 'MIET'})
        assert response.status_code == 201, response.text
        if kind == 'units':
            response = await api.post('/api/units', json={'property_id': response.json()['id'],
                'number': 'T56 empty', 'usage_type': 'WOHNUNG', 'area': '40.00'})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['properties', 'units', 'persons'])
async def test_delete_unused_master_persists_and_does_not_recreate(kind, rent_world):
    async with api_client(rent_world) as api:
        row = await unused(api, kind)
        response = await api.delete(f"/api/{kind}/{row['id']}")
        assert response.status_code == 204, response.text
        assert response.content == b''
    async with api_client(rent_world) as api:
        assert (await api.get(f"/api/{kind}/{row['id']}")).status_code == 404
        assert (await api.delete(f"/api/{kind}/{row['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_delete_unused_lease_then_empty_unit(rent_world):
    w = rent_world
    async with api_client(w) as api:
        original = await api.get('/api/leases', params={'unit_id': str(w['unit'])})
        assert original.status_code == 200 and len(original.json()) == 1
        removed = await api.delete(f"/api/leases/{w['lease']}")
        assert removed.status_code == 204, removed.text
        assert (await api.get('/api/leases', params={'unit_id': str(w['unit'])})).json() == []
        unit = await api.delete(f"/api/units/{w['unit']}")
        assert unit.status_code == 204, unit.text
        assert (await api.get(f"/api/units/{w['unit']}")).status_code == 404
        assert (await api.get(f"/api/persons/{w['person']}")).status_code == 200


def reason(response, *terms):
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert isinstance(detail, str) and any(term.casefold() in detail.casefold() for term in terms), detail


@pytest.mark.asyncio
async def test_delete_property_with_unit_names_reason_without_mutation(rent_world):
    async with api_client(rent_world) as api:
        row = await unused(api, 'units')
        url = f"/api/properties/{row['property_id']}"
        before = (await api.get(url)).json()
        reason(await api.delete(url), 'Einheit', 'unit')
        assert (await api.get(url)).json() == before
        assert (await api.get(f"/api/units/{row['id']}")).json() == row


@pytest.mark.asyncio
async def test_delete_unit_with_lease_names_reason_preserves_original(rent_world):
    w = rent_world
    async with api_client(w) as api:
        unit_url = f"/api/units/{w['unit']}"
        before = (await api.get(unit_url)).json()
        leases = (await api.get('/api/leases', params={'unit_id': str(w['unit'])})).json()
        reason(await api.delete(unit_url), 'Mietvertrag', 'Vertrag', 'lease')
        assert (await api.get(unit_url)).json() == before
        assert (await api.get('/api/leases', params={'unit_id': str(w['unit'])})).json() == leases


@pytest.mark.asyncio
async def test_delete_person_with_profile_preserves_contact(rent_world):
    w = rent_world
    async with api_client(w) as api:
        url = f"/api/persons/{w['person']}"
        before = (await api.get(url)).json()
        reason(await api.delete(url), 'Profil', 'Mieter', 'tenant')
        assert (await api.get(url)).json() == before


@pytest.mark.asyncio
async def test_delete_lease_with_rent_history_preserves_exact_history(rent_world):
    w = rent_world
    async with api_client(w) as api:
        await post_change(api, w, valid_from='2026-02-01', base_rent='760.00')
        history_url = f"/api/leases/{w['lease']}/rent-changes"
        history = (await api.get(history_url)).json()
        assert len(history) == 1
        leases = (await api.get('/api/leases', params={'unit_id': str(w['unit'])})).json()
        reason(await api.delete(f"/api/leases/{w['lease']}"), 'Miethistorie', 'Mietänder', 'history', 'rent')
        assert (await api.get(history_url)).json() == history
        assert (await api.get('/api/leases', params={'unit_id': str(w['unit'])})).json() == leases


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,key', [('properties', 'property'), ('units', 'unit'), ('persons', 'person'), ('leases', 'lease')])
async def test_delete_role_and_tenant_boundary(kind, key, rent_world):
    w = rent_world
    async with api_client(w, actor='reader') as reader, api_client(w, foreign=True) as foreign:
        assert (await reader.delete(f'/api/{kind}/{w[key]}')).status_code == 403
        assert (await foreign.delete(f'/api/{kind}/{w[key]}')).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['properties', 'units', 'persons', 'leases'])
async def test_delete_unknown_id_is_404(kind, rent_world):
    async with api_client(rent_world) as api:
        assert (await api.delete(f'/api/{kind}/{uuid.uuid4()}')).status_code == 404


@pytest.mark.asyncio
async def test_parallel_delete_empty_unit_has_one_effect(rent_world):
    async with api_client(rent_world) as setup:
        row = await unused(setup, 'units')
    url = f"/api/units/{row['id']}"
    async with api_client(rent_world) as first, api_client(rent_world) as second:
        responses = await asyncio.gather(first.delete(url), second.delete(url))
        assert sorted(response.status_code for response in responses) == [204, 404]
        assert (await first.get(url)).status_code == 404


@pytest.mark.asyncio
async def test_delete_live_role_downgrade_does_not_trust_cookie(rent_world, rent_db):
    w = rent_world
    async with api_client(w) as api:
        row = await unused(api, 'units')
        await rent_db.execute(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:admin"), w)
        await rent_db.commit()
        url = f"/api/units/{row['id']}"
        assert (await api.delete(url)).status_code == 403
        assert (await api.get(url)).json() == row


@pytest.mark.asyncio
async def test_delete_imported_ids_preserves_complete_batch_replay(rent_world):
    from tests.test_master_import import apply, body, preview

    async with api_client(rent_world) as api:
        value = body()
        projected = await preview(api, value)
        request_id = uuid.uuid4()
        created = await apply(api, value, projected, request_id)
        assert created.status_code == 201, created.text
        original = created.json()
        endpoints = {'property': 'properties', 'unit': 'units', 'person': 'persons'}
        for kind in endpoints:
            row = next(row for row in original['rows'] if row['kind'] == kind)
            url = f"/api/{endpoints[kind]}/{row['id']}"
            before = await api.get(url)
            assert before.status_code == 200, before.text
            response = await api.delete(url)
            assert response.status_code == 409, response.text
            assert isinstance(response.json()['detail'], str)
            assert (await api.get(url)).json() == before.json()
        replay = await apply(api, value, projected, request_id)
        assert replay.status_code == 201, replay.text
        assert replay.json() == original


@pytest.mark.asyncio
async def test_delete_retained_letter_keeps_lease_and_exact_issued_pdf(rent_world):
    from tests.test_letters import create_template, issue_body

    async with api_client(rent_world) as api:
        template = await create_template(api)
        issued = await api.post('/api/letters/issue', json=issue_body(rent_world, template))
        assert issued.status_code == 201, issued.text
        row = issued.json()[0]
        original = await api.get(row['pdf_url'])
        assert original.status_code == 200 and original.content.startswith(b'%PDF')
        reason(await api.delete(f"/api/leases/{rent_world['lease']}"), 'refer', 'Brief', 'letter')
        assert (await api.get(row['pdf_url'])).content == original.content
        assert (await api.get('/api/leases', params={'unit_id': str(rent_world['unit'])})).json()[0]['id'] == str(rent_world['lease'])


@pytest.mark.asyncio
async def test_delete_append_only_audit_identity_prevents_removal(rent_world, rent_db):
    from app.models.audit_event import AuditEvent

    w = rent_world
    async with api_client(w) as api:
        row = await unused(api, 'units')
        event = AuditEvent(organization_id=w['org'], actor_user_id=w['admin'],
                           entity_type='unit', entity_id=uuid.UUID(row['id']), action='retained_test_source')
        rent_db.add(event)
        await rent_db.commit()
        event_id = event.id
        reason(await api.delete(f"/api/units/{row['id']}"), 'audit', 'histor')
        assert (await api.get(f"/api/units/{row['id']}")).json() == row
        assert await rent_db.scalar(text('SELECT entity_id FROM audit_event WHERE id=:id'), {'id': event_id}) == uuid.UUID(row['id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('exact_reference', [True, False])
async def test_delete_retained_snapshot_uses_exact_structural_uuid(rent_world, rent_db, exact_reference):
    from datetime import date
    from app.models.settlement_run import SettlementRun

    w = rent_world
    async with api_client(w) as api:
        created = await api.post('/api/units', json={'property_id': str(w['property']),
            'number': 'Snapshot-only vacancy', 'usage_type': 'WOHNUNG', 'area': '40.00'})
        assert created.status_code == 201, created.text
        row = created.json()
        payload = {'inputs': {'units': [{'id': row['id']}]} } if exact_reference else {'note': f"Not a reference: prefix{row['id']}suffix"}
        run = SettlementRun(organization_id=w['org'], property_id=w['property'],
            run_type='BK_MIETER', period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
            unit_id=w['unit'], lease_agreement_id=w['lease'], tenant_profile_id=w['tenant'],
            status='draft', engine_version='test', snapshot_payload=payload)
        rent_db.add(run)
        await rent_db.commit()
        run_id = run.id
        response = await api.delete(f"/api/units/{row['id']}")
        if exact_reference:
            reason(response, 'snapshot', 'Abrechnung')
            assert (await api.get(f"/api/units/{row['id']}")).json() == row
        else:
            assert response.status_code == 204, response.text
        assert await rent_db.scalar(text('SELECT snapshot_payload FROM settlement_run WHERE id=:id'), {'id': run_id}) == payload


@pytest.mark.asyncio
async def test_delete_waits_for_shared_financial_input_boundary(rent_world, rent_db):
    w = rent_world
    async with api_client(w) as api:
        row = await unused(api, 'units')
        await rent_db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                              {'key': f"financial-inputs:{w['org']}"})
        blocker = await rent_db.scalar(text('SELECT pg_backend_pid()'))
        task = asyncio.create_task(api.delete(f"/api/units/{row['id']}"))
        waiting = False
        try:
            for _ in range(200):
                waiting = await rent_db.scalar(text("""SELECT EXISTS(SELECT 1 FROM pg_locks l
                    WHERE l.locktype='advisory' AND NOT l.granted
                    AND :blocker=ANY(pg_blocking_pids(l.pid)))"""), {'blocker': blocker})
                if waiting or task.done():
                    break
                await asyncio.sleep(0.01)
        finally:
            await rent_db.rollback()
        response = await asyncio.wait_for(task, timeout=15)
        assert waiting, 'Master deletion must wait for the shared financial-input lock'
        assert response.status_code == 204, response.text


@pytest.mark.asyncio
async def test_delete_source_read_is_not_prepare_even_for_admin(rent_world):
    from tests.test_property_source_access import grant
    from tests.test_property_source_enforcement import synthetic_enforcement

    w = rent_world
    source = {'org_a_id': w['org'], 'user_a_admin_id': w['admin']}
    async with api_client(w) as api:
        row = await unused(api, 'units')
        await grant(api, source, row['property_id'], caps=['read'])
        async with synthetic_enforcement(source):
            url = f"/api/units/{row['id']}"
            assert (await api.get(url)).status_code == 200
            assert (await api.delete(url)).status_code == 404
            assert (await api.get(url)).json() == row


@pytest.mark.asyncio
async def test_delete_migration_roundtrip_preserves_all_rows_security_and_issued_pdf(rent_world):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_letters import create_template, issue_body

    async with api_client(rent_world) as api:
        template = await create_template(api)
        issued = await api.post('/api/letters/issue', json=issue_body(rent_world, template))
        assert issued.status_code == 201, issued.text
        pdf_url = issued.json()[0]['pdf_url']
        original_pdf = (await api.get(pdf_url)).content
        assert original_pdf.startswith(b'%PDF')
    paths = list((Path(__file__).parents[1] / 'migrations/versions').glob('0137_*.py'))
    assert len(paths) == 1
    spec = importlib.util.spec_from_file_location('t56_migration', paths[0])
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ('0137', '0136')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            def snapshot():
                names = connection.execute(text("""SELECT c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relkind IN ('r','p')
                    AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                        AND d.objid=c.oid AND d.deptype='e') ORDER BY c.relname""")).scalars().all()
                rows = {name: connection.execute(text(f'SELECT to_jsonb(t)::text FROM "{name}" t ORDER BY to_jsonb(t)::text')).scalars().all() for name in names}
                security = [connection.execute(text(query)).all() for query in (
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname",
                    "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname",
                    "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY pg_get_triggerdef(t.oid)",
                    "SELECT pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND left(p.proname,14)<>'master_delete_' ORDER BY p.proname,p.oid",
                )]
                return rows, security

            try:
                before = snapshot()
                assert before[0]['lease_agreement'] and before[0]['letter_document']
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
                    assert snapshot() == before
                    assert connection.scalar(text("SELECT to_regprocedure('master_delete_reference_kind(uuid,text,uuid)') IS NULL"))
                    migration.upgrade()
                    assert snapshot() == before
                    assert connection.scalar(text("SELECT has_function_privilege('app_rw','master_delete_reference_kind(uuid,text,uuid)','EXECUTE')"))
                    assert not connection.scalar(text("SELECT has_function_privilege('app_rw','master_delete_json_has_id(jsonb,uuid)','EXECUTE')"))
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    async with api_client(rent_world) as api:
        assert (await api.get(pdf_url)).content == original_pdf
