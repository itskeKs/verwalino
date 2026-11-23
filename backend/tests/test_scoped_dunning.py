"""Dunning snapshots retain exact scope through creation, issue, export and privacy."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_financial_scope_boundary import worker_tenant as worker_tenant, scope_seed as scope_seed
from tests.test_scoped_account_statements import add_payments, fetch, statement_fixture, client, mutate

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
        # Tenant: 12 x (500.00 rent + 100.00 advance) = 7200.00 owed in 2025. June's rent was paid 1000.00 (100.00 + 900.00
        # into the two scopes) for 500.00 owed; the 500.00 over settle the oldest due open position of the lease, which is
        # January 2024's rent (the lease runs since 1 January 2024, nothing was paid for 2024) and outside this notice
        # (#806). 2025 stays open with 7200.00 - 500.00 paid for June's own rent = 6700.00.
        # The owner's Hausgeld account does not set off a surplus.
        assert response.json()['principal_amount'] == ('6700.00' if kind == 'tenant' else '1100.00')
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
        assert notice['principal_amount'] == '6700.00'  # 7200.00 owed in 2025 - 500.00 paid for June's rent, see the first test
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


async def test_scoped_issue_autoselects_only_the_scope_bound_account(migrator_session, scope_seed):
    """Only an active LAUFEND account with IBAN, bound to the notice's scope, is eligible."""
    import io

    import pdfplumber

    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare', 'approve', 'export'])
    async with client(ids) as api:
        created = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert created.status_code == 201, created.text
        notice_id = created.json()['id']
    # An unbound account with an IBAN on the same property must not become
    # eligible for a scoped notice; it appears only after the draft exists,
    # because an unclassified input would block the scoped calculation.
    ids['stray'] = uuid.uuid4()
    await mutate(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label,iban) "
        "VALUES (:stray,:org,:property,'LAUFEND','Ungebunden','DE89370400440532013000')"), ids)
    async with client(ids) as api:
        issued = await api.post(f'/api/dunning/{notice_id}/issue')
        assert issued.status_code == 200, issued.text
        pdf = await api.get(f'/api/dunning/{notice_id}/pdf')
        assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')
        with pdfplumber.open(io.BytesIO(pdf.content)) as doc:
            page_text = "\n".join(page.extract_text() or '' for page in doc.pages)
        # The scope-bound fixture account carries the IBAN (scope_fixture); the
        # unbound account with an IBAN on the same property must not appear.
        assert 'DE02200505501015871393' in page_text
        assert 'DE89370400440532013000' not in page_text


async def test_scoped_selection_freezes_choice_and_refuses_other_scope_accounts(
    migrator_session, scope_seed,
):
    """Two eligible accounts in the notice's scope: explicit selection, the
    chosen one is frozen, an account of another scope is refused."""
    import io

    import pdfplumber

    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    ids['extra'] = uuid.uuid4()
    await mutate(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label,iban) "
        "VALUES (:extra,:org,:property,'LAUFEND','Zweites Konto','DE44120300000000202052')"), ids)
    await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) "
        "VALUES (:org,:property,:scope,:extra,'REVIEWED','Second eligible account',:actor)"), ids)
    # An IBAN-bearing account bound to the OTHER scope must not be selectable
    # for this notice either.
    ids['other_scope_account'] = uuid.uuid4()
    await mutate(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label,iban) "
        "VALUES (:other_scope_account,:org,:property,'LAUFEND','Anderkreis-Konto','DE89370400440532013000')"), ids)
    await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) "
        "VALUES (:org,:property,:second_scope,:other_scope_account,'REVIEWED','Other scope account',:actor)"), ids)
    await grant(ids, ['read', 'prepare', 'approve', 'export'])
    async with client(ids) as api:
        created = await api.post('/api/dunning/tenant', json=tenant_body(ids))
        assert created.status_code == 201, created.text
        notice_id = created.json()['id']
    async with client(ids) as api:
        refused = await api.post(f'/api/dunning/{notice_id}/issue')
        assert refused.status_code == 409, refused.text
        detail = refused.json()['detail']
        assert detail['code'] == 'dunning_bank_selection_required'
        offered = {row['id'] for row in detail['bank_accounts']}
        assert offered == {str(ids['bank']), str(ids['extra'])}

        # An account of another scope is refused: it is neither offered nor
        # visible inside this scope context (foreign id -> 404), and the
        # eligibility filter above excludes it from the candidates.
        foreign = await api.post(
            f'/api/dunning/{notice_id}/issue',
            json={'bank_account_id': str(ids['other_scope_account'])},
        )
        assert foreign.status_code == 404, foreign.text

        chosen = await api.post(
            f'/api/dunning/{notice_id}/issue', json={'bank_account_id': str(ids['extra'])},
        )
        assert chosen.status_code == 200, chosen.text
        pdf = await api.get(f'/api/dunning/{notice_id}/pdf')
        assert pdf.status_code == 200
        with pdfplumber.open(io.BytesIO(pdf.content)) as doc:
            page_text = "\n".join(page.extract_text() or '' for page in doc.pages)
        assert 'DE44120300000000202052' in page_text
        assert 'DE02200505501015871393' not in page_text


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


async def test_other_scopes_escalate_the_level_of_this_scope_but_not_its_amount(migrator_session, scope_seed):
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
        # The issued notice of the first scope counts (its draft does not): level 2 with the level-2 fee (#818, 0203);
        # the principal is the lease's and the same in both scopes.
        assert (other.json()['level'], other.json()['fee_amount'], other.json()['principal_amount']) == (2, '2.50', '6700.00')


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
                            # 0190 patched this function after 0163; the rollback-only roundtrip below
                            # starts from the 0163 body (the surrounding transaction is rolled back).
                            with Operations.context(MigrationContext.configure(sync)):
                                import_module('migrations.versions.0190_ownership_correction').patch_functions(reverse=True)
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


async def tenant_notice(api, ids, period_start, period_end, reference_date, scope='scope'):
    return await api.post('/api/dunning/tenant', json={
        'tenant_profile_id': str(ids['profile']), 'lease_agreement_id': str(ids['lease']), 'accounting_scope_id': str(ids[scope]),
        'period_start': period_start, 'period_end': period_end, 'reference_date': reference_date})


async def test_a_month_paid_across_two_scopes_has_no_arrears_and_no_notice(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare'])
    # June owes 500.00 rent and 100.00 advance. The rent was paid 100.00 into the first scope's account and
    # 900.00 into the second one's, the advance 100.00 into the second one's: nothing is open for June.
    await add_payments(ids, {'amount': '100.00', 'type': 'NK_VORAUSZAHLUNG'})
    async with client(ids) as api:
        june = await tenant_notice(api, ids, '2025-06-01', '2025-06-30', '2025-07-15')
        assert june.status_code == 422, june.text
        assert 'No overdue items' in june.json()['detail']
        assert (await api.get('/api/dunning')).json() == []
        # July has no payment at all: the notice demands 500.00 rent and 100.00 advance, as in every scope.
        july = await tenant_notice(api, ids, '2025-07-01', '2025-07-31', '2025-07-15')
        assert july.status_code == 201, july.text
        assert july.json()['principal_amount'] == '600.00'


async def test_interest_of_a_month_settled_late_in_another_scope_runs_only_until_that_payment(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare'])
    # July rent of 500.00 is due on Thursday 3 July (third working day, § 556b BGB). 100.00 arrived on 1 July in the
    # first scope's account, 300.00 on 21 July in the second one's; 100.00 rent and the 100.00 advance are still open
    # on 15 August.
    await add_payments(
        ids,
        {'amount': '100.00', 'month': 7, 'value_date': date(2025, 7, 1), 'bank': ids['bank'], 'scope': ids['scope']},
        {'amount': '300.00', 'month': 7, 'value_date': date(2025, 7, 21)},
    )
    path = '/api/dunning/settings'
    async with client(ids, 'actor') as admin:
        original = (await admin.get(path)).json()
        settings = {key: value for key, value in original.items() if key != 'base_rate_covered_until'}
        assert (await admin.put(path, json={**settings, 'default_interest_enabled': True})).status_code == 200
    try:
        async with client(ids) as api:
            created = await tenant_notice(api, ids, '2025-07-01', '2025-07-31', '2025-08-15')
            assert created.status_code == 201, created.text
            notice = created.json()
    finally:
        async with client(ids, 'actor') as admin:
            assert (await admin.put(path, json={key: value for key, value in original.items() if key != 'base_rate_covered_until'})).status_code == 200
    rent = next(item for item in notice['interest_snapshot']['items'] if item['payment_type'] == 'KALTMIETE')
    # 400.00 stay unpaid from 4 July, the first day after the due date, until the day before the payment of
    # 21 July (17 days), then 100.00 until 15 August (26 days).
    assert [(row['start'], row['end'], row['days'], row['principal']) for row in rent['segments']] == [
        ('2025-07-04', '2025-07-20', 17, '400.00'), ('2025-07-21', '2025-08-15', 26, '100.00')]
    for row in rent['segments']:
        # Interest = principal x annual rate x days / 365, to within the cent the item's rounding moves between segments.
        exact = Decimal(row['principal']) * Decimal(row['annual_rate']) * row['days'] / 36500
        assert abs(Decimal(row['interest']) - exact) <= Decimal('0.01'), row
    assert notice['principal_amount'] == '200.00'


async def test_a_notice_names_nothing_of_the_payments_of_another_scope(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await grant(ids, ['read', 'prepare', 'approve', 'export'])
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:second_scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    hidden = await fetch('SELECT id::text FROM payment WHERE bank_account_id=:second_bank', ids)
    async with client(ids) as api:
        created = await tenant_notice(api, ids, '2025-01-01', '2025-12-31', '2026-01-15')
        assert created.status_code == 201, created.text
        for response in (created, await api.get(f"/api/dunning/{created.json()['id']}")):
            for secret in (hidden, str(ids['second_bank']), str(ids['second_scope']), 'Second account', 'Second statement sphere'):
                assert secret not in response.text, secret
