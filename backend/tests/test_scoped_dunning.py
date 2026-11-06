"""Dunning snapshots retain exact scope through creation, issue, export and privacy."""
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_account_statements import statement_fixture, client, mutate

pytestmark = pytest.mark.asyncio


@pytest.fixture(params=['tenant', 'owner'])
async def creation_case(request, migrator_session, scope_seed):
    kind = request.param
    ids = await statement_fixture(migrator_session, scope_seed, 'lease' if kind == 'tenant' else 'owner')
    await grant(ids, ['read', 'prepare'])
    body = tenant_body(ids)
    if kind == 'owner':
        body.pop('tenant_profile_id')
        body.pop('lease_agreement_id')
        body['owner_profile_id'] = str(ids['profile'])
        ids['plan'] = uuid.uuid4()
        await mutate(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES (:plan,:org,:property,2025,'2025-01-01','2025-12-31','draft','fixture')"), ids)
        await mutate(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from) VALUES (:org,:unit,:profile,'2025-01-01')"), ids)
        await mutate(text("INSERT INTO economic_plan_line(organization_id,economic_plan_id,line_type,unit_id,owner_profile_id,label,planned_amount,monthly_amount,sort_order) VALUES (:org,:plan,'OWNER_ADVANCE',:unit,:profile,'Plan source',1200,100,1)"), ids)
        await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:plan,'REVIEWED','Plan fixture',:actor)"), ids)
        await mutate(text("UPDATE economic_plan SET status='finalized',finalized_at=now(),finalized_by=:actor,snapshot_payload='{}',snapshot_sha256=:sha WHERE id=:plan"), {**ids, 'sha': 'd'*64})
        body['economic_plan_id'] = str(ids['plan'])
    return ids, kind, body


async def test_created_draft_is_readable_at_response_start(creation_case, app_rw_session):
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    ids, kind, body = creation_case
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true), set_config('app.current_user_id',:actor,true)"),
                                 {'org': str(ids['org']), 'actor': str(ids['clerk'])})
    observed = []
    async with client(ids) as api:
        async def observe_response(scope, receive, send):
            async def observe_send(message):
                if message['type'] == 'http.response.start' and message['status'] == 201:
                    # Independent app_rw transaction: ASGITransport otherwise waits
                    # for request-scope cleanup and hides this response-boundary race.
                    rows = (await app_rw_session.scalars(text(f'SELECT id FROM dunning_notice WHERE {kind}_profile_id=:profile'), {'profile': ids['profile']})).all()
                    observed.extend(str(row) for row in rows)
                    for notice_id in observed:
                        detail = await api.get(f'/api/dunning/{notice_id}')
                        assert detail.status_code == 200, detail.text
                        assert detail.json()['status'] == 'draft'
                await send(message)
            await app(scope, receive, observe_send)

        async with AsyncClient(transport=ASGITransport(app=observe_response), base_url='http://test', cookies=api.cookies) as writer:
            response = await writer.post(f'/api/dunning/{kind}', json=body)
        assert response.status_code == 201, response.text
        assert observed == [response.json()['id']], 'Draft not committed at http.response.start'
        assert response.json()['principal_amount'] == ('7100.00' if kind == 'tenant' else '1100.00')
        assert [row['id'] for row in (await api.get('/api/dunning')).json()] == observed


async def test_failed_creation_rolls_back_flushed_draft(creation_case, monkeypatch):
    from fastapi import HTTPException
    from app.services.dunning_service import DunningService

    ids, kind, body = creation_case
    method = f'create_dunning_for_{kind}'
    original = getattr(DunningService, method)

    async def refuse_after_flush(service, **values):
        await original(service, **values)
        raise HTTPException(422, 'Synthetic late validation refusal')

    monkeypatch.setattr(DunningService, method, refuse_after_flush)
    async with client(ids) as api:
        response = await api.post(f'/api/dunning/{kind}', json=body)
        assert response.status_code == 422, response.text
        assert (await api.get('/api/dunning')).json() == []
        monkeypatch.setattr(DunningService, method, original)
        created = await api.post(f'/api/dunning/{kind}', json=body)
        assert created.status_code == 201, created.text
        assert len((await api.get('/api/dunning')).json()) == 1


async def grant(ids, caps, key='scope'):
    params = {**ids, 'sphere': ids[key], 'caps': caps}
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:sphere AND user_id=:clerk AND revoked_at IS NULL'), params)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:sphere,:clerk,:caps,:actor,'Dunning test grant')"), params)


def tenant_body(ids):
    return {'tenant_profile_id': str(ids['profile']), 'lease_agreement_id': str(ids['lease']),
            'accounting_scope_id': str(ids['scope']), 'period_start': '2025-01-01',
            'period_end': '2025-12-31', 'reference_date': '2026-01-15'}


async def legacy_notice(ids):
    ids['notice'] = uuid.uuid4()
    await mutate(text("""INSERT INTO dunning_notice(id,organization_id,tenant_profile_id,level,issue_date,due_date,
      principal_amount,fee_amount,total_amount,included_items,status,issued_at,issued_by)
      VALUES (:notice,:org,:profile,1,'2026-01-15','2026-01-30',7000,0,7000,'[]','issued',now(),:actor)"""), ids)


async def test_ambiguous_legacy_notice_cannot_leak_or_disappear_from_privacy(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await legacy_notice(ids)
    async with client(ids, 'actor') as api:
        for suffix in ['', '/pdf']:
            assert (await api.get(f"/api/dunning/{ids['notice']}{suffix}")).status_code == 404
        assert all(row['id'] != str(ids['notice']) for row in (await api.get('/api/dunning')).json())
        assert (await api.get(f"/api/dsgvo/persons/{ids['person']}/status")).status_code == 409
        assert (await api.get(f"/api/dsgvo/persons/{ids['person']}/export")).status_code == 409


async def test_scoped_dunning_distinguishes_prepare_approve_export_and_revocation(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare'])
    async with client(ids) as api:
        created = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert created.status_code == 201, created.text
        notice = created.json()
        assert notice['principal_amount'] == '7100.00'
        assert notice['accounting_scope_id'] == str(ids['scope'])
        assert (await api.post(f"/api/dunning/{notice['id']}/issue")).status_code == 404
    async with client(ids, 'actor') as admin:
        assert (await admin.get(f"/api/dunning/{notice['id']}")).status_code == 404
    await grant(ids, ['read', 'approve'])
    async with client(ids) as api:
        assert (await api.post(f"/api/dunning/{notice['id']}/issue")).status_code == 200
        assert (await api.get(f"/api/dunning/{notice['id']}/pdf")).status_code == 404
    await grant(ids, ['read', 'export'])
    async with client(ids) as api:
        pdf = await api.get(f"/api/dunning/{notice['id']}/pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    async with client(ids) as api:
        assert (await api.get(f"/api/dunning/{notice['id']}")).status_code == 404
        assert (await api.get(f"/api/dunning/{notice['id']}/pdf")).status_code == 404


async def test_unclassified_escalation_history_blocks_scoped_draft(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await legacy_notice(ids)
    await grant(ids, ['read', 'prepare'])
    async with client(ids) as api:
        response = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert response.status_code == 409
        assert 'history' in response.json()['detail']


async def test_scoped_draft_cancellation_retains_context_and_cannot_be_issued(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare', 'approve'])
    async with client(ids) as api:
        response = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert response.status_code == 201, response.text
        notice_id = response.json()['id']
        assert (await api.delete(f'/api/dunning/{notice_id}')).status_code == 204
        cancelled = await api.get(f'/api/dunning/{notice_id}')
        assert cancelled.json()['status'] == 'cancelled'
        assert cancelled.json()['accounting_scope_id'] == str(ids['scope'])
        assert (await api.post(f'/api/dunning/{notice_id}/issue')).status_code == 409
        assert (await api.delete(f'/api/dunning/{notice_id}')).status_code == 204
        assert not any(row['id'] == notice_id for row in (await api.get('/api/dunning?status=draft')).json())


async def test_other_scopes_do_not_escalate_this_scope(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare', 'approve'])
    await grant(ids, ['read', 'prepare', 'approve'], 'second_scope')
    async with client(ids) as api:
        first = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert first.status_code == 201, first.text
        assert (await api.post('/api/dunning/'+first.json()['id']+'/issue')).status_code == 200
        same = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert same.status_code == 201 and same.json()['level'] == 2
        other = await api.post('/api/dunning/tenant', json={**tenant_body(ids), 'accounting_scope_id': str(ids['second_scope'])})
        assert other.status_code == 201, other.text
        assert other.json()['level'] == 1 and other.json()['principal_amount'] == '6700.00'


async def test_tenant_recipient_must_match_selected_lease(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare'])
    ids['wrong_tenant'] = uuid.uuid4()
    await mutate(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:wrong_tenant,:org,:person,'Other profile')"), ids)
    async with client(ids) as api:
        result = await api.post('/api/dunning/tenant', json={**tenant_body(ids), 'tenant_profile_id': str(ids['wrong_tenant'])})
        assert result.status_code == 422
        assert 'recipient must match' in result.json()['detail']


async def test_owner_scope_requires_bound_finalized_plan(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'owner')
    await grant(ids, ['read', 'prepare'])
    body = {'owner_profile_id': str(ids['profile']), 'accounting_scope_id': str(ids['scope']),
            'period_start': '2025-01-01', 'period_end': '2025-12-31', 'reference_date': '2026-01-15'}
    async with client(ids) as api:
        result = await api.post('/api/dunning/owner', json=body)
        assert result.status_code == 422
        assert 'finalized economic plan' in result.json()['detail']
    ids['plan'] = uuid.uuid4()
    await mutate(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES (:plan,:org,:property,2025,'2025-01-01','2025-12-31','draft','fixture')"), ids)
    await mutate(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from) VALUES (:org,:unit,:profile,'2025-01-01')"), ids)
    await mutate(text("INSERT INTO economic_plan_line(organization_id,economic_plan_id,line_type,unit_id,owner_profile_id,label,planned_amount,monthly_amount,sort_order) VALUES (:org,:plan,'OWNER_ADVANCE',:unit,:profile,'Plan source',1200,100,1)"), ids)
    await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:plan,'REVIEWED','Plan fixture',:actor)"), ids)
    async with client(ids) as api:
        result = await api.post('/api/dunning/owner', json={**body, 'economic_plan_id': str(ids['plan'])})
        assert result.status_code == 422
    await mutate(text("UPDATE economic_plan SET status='finalized',finalized_at=now(),finalized_by=:actor,snapshot_payload='{}',snapshot_sha256=:sha WHERE id=:plan"), {**ids, 'sha': 'd'*64})
    async with client(ids) as api:
        result = await api.post('/api/dunning/owner', json={**body, 'economic_plan_id': str(ids['plan'])})
        assert result.status_code == 201, result.text
        assert result.json()['principal_amount'] == '1100.00'


async def test_lease_must_overlap_requested_period(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare'])
    await mutate(text("UPDATE lease_agreement SET valid_to='2025-01-01' WHERE id=:lease"), ids)
    async with client(ids) as api:
        response = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert response.status_code == 422 and 'overlap' in response.text
        assert (await api.get('/api/dunning')).json() == []


async def test_raw_principal_cannot_bypass_provenance_or_lease_overlap(migrator_session, scope_seed):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import APP_RW_URL
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare'])
    await mutate(text("UPDATE lease_agreement SET valid_to='2025-01-01' WHERE id=:lease"), ids)
    insert_notice = text("""INSERT INTO dunning_notice(id,organization_id,tenant_profile_id,level,issue_date,due_date,
        principal_amount,fee_amount,total_amount,included_items,status)
        VALUES (:notice,:org,:profile,1,'2026-01-15','2026-01-30',100,0,100,'[]','draft')""")
    insert_context = text("""INSERT INTO dunning_scope_context(organization_id,dunning_notice_id,scope_id,
        lease_agreement_id,period_start,period_end,created_by,reason,origin)
        VALUES (:org,:notice,:scope,:lease,'2025-01-01','2025-12-31',:clerk,'Raw principal test',:origin)""")
    engine = create_async_engine(APP_RW_URL)
    try:
        for selected, origin, expected in [(False, None, 'capability'), (True, None, 'capability'),
                                            (True, 'GUESSED', 'check constraint'), (True, 'PREPARED', 'lease and counterparty')]:
            with pytest.raises(DBAPIError) as denied:
                async with engine.begin() as connection:
                    await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true),set_config('app.current_scope_id',:scope,true)"),
                        {'org': str(ids['org']), 'user': str(ids['clerk']), 'scope': str(ids['scope']) if selected else ''})
                    params = {**ids, 'notice': uuid.uuid4(), 'origin': origin}
                    if origin:
                        await connection.execute(insert_context, params)
                    await connection.execute(insert_notice, params)
                    await connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            assert expected in str(denied.value).lower()
    finally:
        await engine.dispose()
    async with client(ids) as api:
        assert (await api.get('/api/dunning')).json() == []


async def test_scope_lookup_audit_privacy_immutability_and_downgrade_guard(migrator_session, scope_seed):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import APP_RW_URL, MIGRATOR_URL, MIGRATOR_SYNC_URL
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare', 'approve', 'export'])
    await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE organization_id=:org AND user_id=:clerk"), ids)
    async with client(ids) as api:
        created = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert created.status_code == 201, created.text
        ids['notice'] = uuid.UUID(created.json()['id'])
        assert (await api.post(f"/api/dunning/{ids['notice']}/issue")).status_code == 200
        assert (await api.get(f"/api/dsgvo/persons/{ids['person']}/export")).status_code == 409
        await grant(ids, ['read', 'export'], key='second_scope')
        assert (await api.get(f"/api/dsgvo/persons/{ids['person']}/export")).status_code == 200
    engine = create_async_engine(APP_RW_URL)
    try:
        for user, visible in [('clerk', True), ('actor', False)]:
            async with engine.begin() as connection:
                await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                    {'org': str(ids['org']), 'user': str(ids[user])})
                count = await connection.scalar(text('SELECT count(*) FROM audit_event WHERE entity_id=:notice'), ids)
                assert (count > 0) == visible
    finally:
        await engine.dispose()
    # The internal lookup is intentionally not executable by app_rw; audit RLS
    # consumes it through its security-definer wrapper.
    lookup_engine = create_async_engine(MIGRATOR_URL)
    try:
        async with lookup_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(ids['org'])})
            assert await connection.scalar(text("SELECT financial_scope_id(:org,'dunning_notice',:notice)"), ids) == ids['scope']
    finally:
        await lookup_engine.dispose()
    # Even the trusted migration principal cannot rewrite persisted provenance.
    engine = create_async_engine(MIGRATOR_URL)
    try:
        for sql in ['UPDATE dunning_scope_context SET reason=\'Replaced\' WHERE dunning_notice_id=:notice',
                    'DELETE FROM dunning_scope_context WHERE dunning_notice_id=:notice']:
            with pytest.raises(DBAPIError, match='immutable'):
                async with engine.begin() as connection:
                    await connection.execute(text(sql), ids)
    finally:
        await engine.dispose()
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('dunning_migration', Path(__file__).parents[1] / 'migrations/versions/0039_dunning_scope_context.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    sync_engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with pytest.raises(DBAPIError, match='Cannot downgrade'):
            with sync_engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
    finally:
        sync_engine.dispose()
    await grant(ids, ['read'])
    async with client(ids) as api:
        assert (await api.get(f"/api/dsgvo/persons/{ids['person']}/export")).status_code == 409


async def test_inherited_selector_scope_requires_requested_live_capability(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    path = f"/api/accounting-scopes/{ids['scope']}"
    async with client(ids) as api:
        assert (await api.get(path, params={'capability': 'read'})).status_code == 200
        assert (await api.get(path, params={'capability': 'prepare'})).status_code == 404
    async with client(ids, 'actor') as admin:
        assert (await admin.get(path, params={'capability': 'prepare'})).status_code == 404
    await grant(ids, ['read', 'prepare'])
    async with client(ids) as api:
        assert (await api.get(path, params={'capability': 'prepare'})).status_code == 200


async def test_raw_dated_successor_guard_keeps_plan_scope_period_and_recipient_boundaries(migrator_session, scope_seed):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_URL
    ids = await statement_fixture(migrator_session, scope_seed, 'owner')
    ids.update(plan=uuid.uuid4(), buyer=uuid.uuid4(), outsider=uuid.uuid4(), draft=uuid.uuid4())
    for owner in ('buyer', 'outsider'):
        await mutate(text('INSERT INTO owner_profile(id,organization_id,person_id) VALUES (:id,:org,:person)'),
                     {**ids, 'id': ids[owner]})
    await mutate(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from,valid_to) VALUES (:org,:unit,:profile,'2020-01-01','2025-07-01'),(:org,:unit,:buyer,'2025-07-01',NULL)"), ids)
    for source in ('plan', 'draft'):
        await mutate(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES (:id,:org,:property,2025,'2025-01-01','2025-12-31','draft','fixture')"), {**ids, 'id': ids[source]})
        await mutate(text("INSERT INTO economic_plan_line(organization_id,economic_plan_id,line_type,unit_id,owner_profile_id,label,planned_amount,monthly_amount) VALUES (:org,:id,'OWNER_ADVANCE',:unit,:profile,'Original advance',4800,400)"), {**ids, 'id': ids[source]})
        await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:id,'REVIEWED','Direct guard source',:actor)"), {**ids, 'id': ids[source]})
    await mutate(text("UPDATE economic_plan SET status='finalized',finalized_at=now(),finalized_by=:actor,snapshot_payload='{}',snapshot_sha256=:sha WHERE id=:plan"), {**ids, 'sha': 'a'*64})
    engine = create_async_engine(MIGRATOR_URL)
    try:
        for owner, source, scope, start, accepted in [
            ('buyer', 'plan', 'scope', '2025-07-01', True),
            ('profile', 'plan', 'scope', '2025-01-01', True),
            ('outsider', 'plan', 'scope', '2025-07-01', False),
            ('buyer', 'draft', 'scope', '2025-07-01', False),
            ('buyer', 'plan', 'second_scope', '2025-07-01', False),
            ('buyer', 'plan', 'scope', '2024-12-01', False),
            ('buyer', 'plan', 'scope', '2025-01-01', False),
        ]:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    params = {**ids, 'notice': uuid.uuid4(), 'owner': ids[owner], 'selected': ids[scope],
                              'source': ids[source], 'start': date.fromisoformat(start),
                              'end': date(2025, 6, 30) if owner == 'buyer' and start == '2025-01-01' else date(2025, 12, 31)}
                    await connection.execute(text("INSERT INTO dunning_notice(id,organization_id,owner_profile_id,level,issue_date,due_date,principal_amount,fee_amount,total_amount,included_items,status) VALUES (:notice,:org,:owner,1,'2026-01-15','2026-01-30',2400,0,2400,'[]','draft')"), params)
                    await connection.execute(text("INSERT INTO dunning_scope_context(organization_id,dunning_notice_id,scope_id,economic_plan_id,period_start,period_end,created_by,reason,origin) VALUES (:org,:notice,:selected,:source,:start,:end,:actor,'Direct successor guard','PREPARED')"), params)
                    if accepted:
                        await connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                        from importlib import import_module
                        from alembic.migration import MigrationContext
                        from alembic.operations import Operations
                        def check_downgrade(sync):
                            module = import_module('migrations.versions.0163_dated_successor_dunning')
                            before = sync.scalar(text("SELECT pg_get_functiondef('validate_dunning_context()'::regprocedure)"))
                            contexts_sql = text('SELECT to_jsonb(c) FROM dunning_scope_context c ORDER BY organization_id,dunning_notice_id')
                            before_contexts = sync.execute(contexts_sql).all()
                            functions_sql = text("""SELECT p.proname,pg_get_function_identity_arguments(p.oid),
                                pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner)
                                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                                WHERE n.nspname='public' AND p.proname IN
                                  ('validate_dunning_context','validate_dunning_context_before0163') ORDER BY 1,2""")
                            before_functions = sync.execute(functions_sql).all()
                            point = sync.begin_nested()
                            try:
                                with Operations.context(MigrationContext.configure(sync)):
                                    unrelated = sync.execute(text("""SELECT c.dunning_notice_id
                                        FROM dunning_scope_context c JOIN dunning_notice n
                                          ON n.id=c.dunning_notice_id AND n.organization_id=c.organization_id
                                        WHERE n.id<>:notice AND c.economic_plan_id IS NOT NULL
                                          AND n.owner_profile_id IS NOT NULL
                                          AND NOT EXISTS(SELECT 1 FROM economic_plan_line ln
                                            WHERE ln.organization_id=c.organization_id AND ln.economic_plan_id=c.economic_plan_id
                                              AND ln.owner_profile_id=n.owner_profile_id AND ln.line_type='OWNER_ADVANCE')"""),
                                        {'notice': params['notice']}).scalars().all()
                                    if unrelated:
                                        with pytest.raises(RuntimeError, match='successor dunning provenance'):
                                            module.downgrade()
                                        assert sync.execute(contexts_sql).all() == before_contexts
                                        assert sync.execute(functions_sql).all() == before_functions
                                        # Isolate this probe from earlier scenarios only
                                        # inside its rollback-only historical projection.
                                        sync.execute(text('ALTER TABLE dunning_scope_context DISABLE TRIGGER USER'))
                                        sync.execute(text('DELETE FROM dunning_scope_context WHERE dunning_notice_id=ANY(:ids)'),
                                                     {'ids': unrelated})
                                        sync.execute(text('ALTER TABLE dunning_scope_context ENABLE TRIGGER USER'))
                                    if owner == 'buyer':
                                        with pytest.raises(RuntimeError, match='successor dunning provenance'):
                                            module.downgrade()
                                    else:
                                        module.downgrade()
                                        restored = sync.scalar(text("SELECT pg_get_functiondef('validate_dunning_context()'::regprocedure)"))
                                        assert module.DATED not in restored and module.ORIGINAL in restored
                                        module.upgrade()
                                assert sync.scalar(text("SELECT pg_get_functiondef('validate_dunning_context()'::regprocedure)")) == before
                            finally:
                                point.rollback()
                            assert sync.execute(contexts_sql).all() == before_contexts
                            assert sync.execute(functions_sql).all() == before_functions
                        await connection.run_sync(check_downgrade)

                    else:
                        with pytest.raises(DBAPIError) as denied:
                            await connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                        assert denied.value.orig.sqlstate == '23514'
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()
