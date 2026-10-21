"""T51 CSV master data: preview is read-only; one atomic, replay-safe import."""
import asyncio
import csv
import io
import uuid

import pytest

from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401

ROOT = '/api/properties/import'
FIELDS = ('kind', 'key', 'property_key', 'name', 'street', 'zip_code', 'city',
          'management_type', 'number', 'area', 'heated_area', 'usage_type',
          'first_name', 'last_name', 'email', 'phone', 'company_name')


def csv_text(rows):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=FIELDS, delimiter=';', lineterminator='\r\n')
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def example_rows():
    return [
        {'kind': 'property', 'key': 'P1', 'name': 'T51 CSV Objekt', 'street': 'CSV-Weg 51',
         'zip_code': '12345', 'city': 'Teststadt', 'management_type': 'MIET'},
        *({'kind': 'unit', 'key': f'U{i}', 'property_key': 'P1', 'number': str(i),
           'area': '60,25', 'heated_area': '55.00', 'usage_type': 'WOHNUNG'} for i in range(1, 5)),
        *({'kind': 'person', 'key': f'C{i}', 'first_name': f'CSV Ada {i}',
           'last_name': 'Synthetic', 'email': f'csv-{i}@example.invalid'} for i in range(1, 5)),
    ]


def body(rows=None):
    return {'csv_text': csv_text(example_rows() if rows is None else rows), 'data_audience': None}


async def preview(api, value=None):
    value = body() if value is None else value
    response = await api.post(ROOT + '/preview', json=value)
    assert response.status_code == 200, response.text
    return response.json()


async def apply(api, value, projected, request_id=None):
    return await api.post(ROOT + '/apply', json={**value,
        'expected_digest': projected['source_digest'], 'request_id': str(request_id or uuid.uuid4())})


async def inventory(api):
    result = {}
    for kind, endpoint in [('properties', '/api/properties'), ('units', '/api/units'), ('persons', '/api/persons')]:
        response = await api.get(endpoint)
        assert response.status_code == 200, response.text
        result[kind] = sorted(response.json(), key=lambda row: row['id'])
    return result


@pytest.mark.asyncio
async def test_master_import_downloadable_template(rent_world):
    async with api_client(rent_world) as api:
        response = await api.get(ROOT + '/template.csv')
    assert response.status_code == 200, response.text
    assert 'attachment' in response.headers['content-disposition']
    parsed = csv.DictReader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';')
    assert tuple(parsed.fieldnames) == FIELDS
    assert {row['kind'] for row in parsed} == {'property', 'unit', 'person'}


@pytest.mark.asyncio
async def test_master_import_preview_is_read_only_then_persists_one_four_four(rent_world):
    async with api_client(rent_world) as api:
        before = await inventory(api)
        value = body()
        projected = await preview(api, value)
        assert projected['errors'] == []
        assert projected['counts'] == {'properties': 1, 'units': 4, 'persons': 4}
        assert len(projected['rows']) == 9
        assert await inventory(api) == before
        response = await apply(api, value, projected)
        assert response.status_code == 201, response.text
        result = response.json()
        assert result['counts'] == projected['counts']
        assert len(result['rows']) == 9
        ids = {row['key']: row['id'] for row in result['rows']}
        assert len(set(ids.values())) == 9
        prop = await api.get('/api/properties/' + ids['P1'])
        assert prop.status_code == 200, prop.text
        assert prop.json()['street'] == 'CSV-Weg 51'
        for i in range(1, 5):
            unit = await api.get('/api/units/' + ids[f'U{i}'])
            assert unit.status_code == 200, unit.text
            assert unit.json()['property_id'] == ids['P1']
            assert unit.json()['area'] == '60.25'
            person = await api.get('/api/persons/' + ids[f'C{i}'])
            assert person.status_code == 200, person.text
            assert person.json()['email'] == f'csv-{i}@example.invalid'
        after = await inventory(api)
        for kind, increase in [('properties', 1), ('units', 4), ('persons', 4)]:
            assert len(after[kind]) == len(before[kind]) + increase
        own_ids = set(ids.values())
        assert {kind: [row for row in rows if row['id'] not in own_ids]
                for kind, rows in after.items()} == before


@pytest.mark.asyncio
@pytest.mark.parametrize('row,field', [
    ({'kind': 'unit', 'key': 'BAD', 'property_key': 'unknown', 'number': '1', 'usage_type': 'WOHNUNG'}, 'property_key'),
    ({'kind': 'property', 'key': 'BAD', 'name': 'X', 'management_type': 'UNKNOWN'}, 'management_type'),
    ({'kind': 'person', 'key': 'BAD', 'last_name': ''}, 'last_name'),
    ({'kind': 'person', 'key': 'BAD', 'last_name': 'X' * 256}, 'last_name'),
    ({'kind': 'person', 'key': 'BAD', 'last_name': 'X', 'email': 'bad\nheader@example.invalid'}, 'email'),
    ({'kind': 'owner', 'key': 'BAD', 'name': 'X'}, 'kind'),
    *[({'kind': 'unit', 'key': 'BAD', 'property_key': 'P1', 'number': '5',
        'usage_type': 'WOHNUNG', 'area': amount}, 'area')
      for amount in ('NaN', 'Infinity', '-1.00', '1.234', '1e4')],
])
async def test_master_import_row_errors_never_partially_apply(rent_world, row, field):
    async with api_client(rent_world) as api:
        before = await inventory(api)
        value = body(example_rows() + [row])
        projected = await preview(api, value)
        assert any(error['line'] == 11 and error['field'] == field for error in projected['errors']), projected
        response = await apply(api, value, projected)
        assert response.status_code == 422, response.text
        assert await inventory(api) == before


@pytest.mark.asyncio
async def test_master_import_explicit_audience_preview_is_read_only_and_replay_rechecks_access(scope_seed, migrator_session):
    from tests.test_property_master_access import principals, audience
    from tests.test_property_source_enforcement import synthetic_enforcement
    from tests.test_scoped_account_statements import client
    await migrator_session.commit()
    ids = principals(scope_seed)
    async with client(ids, 'actor') as api, synthetic_enforcement(scope_seed):
        value = {**body(), 'data_audience': audience(scope_seed)}
        before = await api.get('/api/property-data-domains/page')
        assert before.status_code == 200, before.text
        projected = await preview(api, value)
        assert projected['errors'] == []
        assert (await api.get('/api/property-data-domains/page')).json() == before.json()
        key = uuid.uuid4()
        response = await apply(api, value, projected, key)
        assert response.status_code == 201, response.text
        assert response.json()['counts'] == {'properties': 1, 'units': 4, 'persons': 4}
        property_id = next(row['id'] for row in response.json()['rows'] if row['kind'] == 'property')
        domains = await api.get('/api/property-data-domains/page', params={'property_id': property_id})
        domain = domains.json()['items'][0]['id']
        grants = await api.get(f'/api/property-data-domains/{domain}/grants/page')
        grant_id = grants.json()['items'][0]['id']
        revoked = await api.post(f'/api/property-data-domains/{domain}/grants/{grant_id}/revoke')
        assert revoked.status_code == 204, revoked.text
        replay = await apply(api, value, projected, key)
        assert replay.status_code == 404, replay.text
        assert (await api.get(f'/api/properties/{property_id}')).status_code == 404


@pytest.mark.asyncio
async def test_master_import_migration_preserves_old_data_security_and_issued_original(rent_world):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_letters import create_template, issue_body

    async with api_client(rent_world) as api:
        value = body()
        projected = await preview(api, value)
        request_id = uuid.uuid4()
        imported = await apply(api, value, projected, request_id)
        assert imported.status_code == 201, imported.text
        template = await create_template(api)
        issued = await api.post('/api/letters/issue', json=issue_body(rent_world, template))
        assert issued.status_code == 201, issued.text
        pdf_url = issued.json()[0]['pdf_url']
        original_pdf = (await api.get(pdf_url)).content
        assert original_pdf.startswith(b'%PDF')
    spec = importlib.util.spec_from_file_location('master_import_migration',
        Path(__file__).parents[1] / 'migrations/versions/0134_master_import.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            def snapshot():
                tables = connection.execute(text("""SELECT c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relkind IN ('r','p')
                    AND c.relname <> 'master_import_batch'
                    AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                        AND d.objid=c.oid AND d.deptype='e') ORDER BY c.relname""")).scalars().all()
                rows = {name: connection.execute(text(
                    f'SELECT to_jsonb(t)::text FROM "{name}" t ORDER BY to_jsonb(t)::text'
                )).scalars().all() for name in tables}
                security = [connection.execute(text(query)).all() for query in (
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname <> 'master_import_batch' ORDER BY c.relname",
                    "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' AND tablename <> 'master_import_batch' ORDER BY tablename,policyname",
                    "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND c.relname <> 'master_import_batch' ORDER BY pg_get_triggerdef(t.oid)",
                    "SELECT pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND p.proname <> 'protect_master_import_batch' ORDER BY p.proname,p.oid",
                )]
                return rows, security

            try:
                before = snapshot()
                assert before[0]['lease_agreement'] and before[0]['letter_document']
                with Operations.context(MigrationContext.configure(connection)):
                    with pytest.raises(DBAPIError):
                        with connection.begin_nested():
                            migration.downgrade()
                    assert snapshot() == before
                    connection.execute(text('TRUNCATE master_import_batch'))
                    migration.downgrade()
                    assert connection.scalar(text("SELECT to_regclass('public.master_import_batch')")) is None
                    assert snapshot() == before
                    migration.upgrade()
                    assert snapshot() == before
                    assert connection.scalar(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='master_import_batch'::regclass"))
                    assert connection.scalar(text("SELECT count(*) FROM pg_policies WHERE tablename='master_import_batch'")) >= 2
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    async with api_client(rent_world) as api:
        replay = await apply(api, value, projected, request_id)
        assert replay.status_code == 201 and replay.json() == imported.json()
        assert (await api.get(pdf_url)).content == original_pdf


@pytest.mark.asyncio
async def test_master_import_batch_is_actor_scoped_and_immutable(rent_world):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.core.db import get_tenant_session
    async with api_client(rent_world) as api:
        value = body()
        projected = await preview(api, value)
        response = await apply(api, value, projected)
        assert response.status_code == 201, response.text
        actor = str(rent_world['admin'])
    async with get_tenant_session(rent_world['org']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': actor})
        assert await db.scalar(text('SELECT count(*) FROM master_import_batch')) == 1
        for statement in ('UPDATE master_import_batch SET result=\'{}\'::jsonb',
                          'DELETE FROM master_import_batch'):
            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(text(statement))
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),
                         {'actor': str(uuid.uuid4())})
        assert await db.scalar(text('SELECT count(*) FROM master_import_batch')) == 0


@pytest.mark.asyncio
async def test_master_import_duplicate_keys_and_headers_are_reported(rent_world):
    async with api_client(rent_world) as api:
        value = body(example_rows() + [example_rows()[0]])
        projected = await preview(api, value)
        assert any(error['field'] == 'key' for error in projected['errors'])
        bad_header = {**body(), 'csv_text': body()['csv_text'].replace('kind;key;', 'kind;kind;', 1)}
        projected = await preview(api, bad_header)
        assert any(error['field'] == 'header' for error in projected['errors'])


@pytest.mark.asyncio
async def test_master_import_stale_preview_and_changed_request_conflict(rent_world):
    async with api_client(rent_world) as api:
        value = body()
        projected = await preview(api, value)
        changed = {**value, 'csv_text': value['csv_text'].replace('T51 CSV Objekt', 'Different object')}
        response = await apply(api, changed, projected)
        assert response.status_code == 409, response.text
        key = uuid.uuid4()
        response = await apply(api, value, projected, key)
        assert response.status_code == 201, response.text
        other = await preview(api, changed)
        response = await apply(api, changed, other, key)
        assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_master_import_parallel_replay_creates_only_one_batch(rent_world):
    async with api_client(rent_world) as first, api_client(rent_world) as second:
        value = body()
        projected = await preview(first, value)
        key = uuid.uuid4()
        before = await inventory(first)
        responses = await asyncio.gather(apply(first, value, projected, key), apply(second, value, projected, key))
        assert [r.status_code for r in responses] == [201, 201], [r.text for r in responses]
        assert responses[0].json() == responses[1].json()
        after = await inventory(first)
        assert len(after['properties']) == len(before['properties']) + 1
        assert len(after['units']) == len(before['units']) + 4
        assert len(after['persons']) == len(before['persons']) + 4


@pytest.mark.asyncio
async def test_master_import_read_only_cannot_preview_or_apply(rent_world):
    async with api_client(rent_world, actor='reader') as api:
        response = await api.post(ROOT + '/preview', json=body())
        assert response.status_code == 403, response.text
        response = await api.post(ROOT + '/apply', json={**body(), 'request_id': str(uuid.uuid4()), 'expected_digest': '0' * 64})
        assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_master_import_late_contact_failure_rolls_back_entire_batch(rent_world, monkeypatch):
    from app.repositories.person_repo import PersonRepository
    original = PersonRepository.create
    calls = 0

    async def fail_second(repo, entity):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('Synthetic second contact insert failure')
        return await original(repo, entity)

    async with api_client(rent_world) as api:
        before = await inventory(api)
        value = body()
        projected = await preview(api, value)
        key = uuid.uuid4()
        with monkeypatch.context() as patch:
            patch.setattr(PersonRepository, 'create', fail_second)
            with pytest.raises(RuntimeError, match='Synthetic second contact insert failure'):
                await apply(api, value, projected, key)
        assert calls == 2
        assert await inventory(api) == before
        retry = await apply(api, value, projected, key)
        assert retry.status_code == 201, retry.text
        assert retry.json()['counts'] == {'properties': 1, 'units': 4, 'persons': 4}


@pytest.mark.asyncio
async def test_master_import_foreign_property_reference_is_not_admitted(rent_world):
    async with api_client(rent_world, foreign=True) as foreign:
        properties = await foreign.get('/api/properties')
        assert properties.status_code == 200, properties.text
        foreign_id = properties.json()[0]['id']
    async with api_client(rent_world) as api:
        before = await inventory(api)
        value = body([{'kind': 'unit', 'key': 'FOREIGN', 'property_key': foreign_id,
                       'number': 'X', 'usage_type': 'WOHNUNG'}])
        response = await api.post(ROOT + '/preview', json=value)
        assert response.status_code == 404, response.text
        response = await api.post(ROOT + '/apply', json={**value, 'expected_digest': '0' * 64,
                                                       'request_id': str(uuid.uuid4())})
        assert response.status_code == 404, response.text
        assert await inventory(api) == before
