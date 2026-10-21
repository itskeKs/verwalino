"""Deadline monitor never derives obligations from another accounting sphere."""
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_account_statements import statement_fixture, client, mutate

pytestmark = pytest.mark.asyncio


async def add_cost(ids, scope, start='2025-01-01', end='2025-12-31'):
    params = {**ids, 'cost': uuid.uuid4(), 'sphere': ids[scope], 'start': date.fromisoformat(start), 'end': date.fromisoformat(end)}
    await mutate(text("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,cost_category_plan_version,
        invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source,confirmed_at,confirmed_by)
        SELECT :cost,:org,:property,id,1,:start,:start,:end,100,'UMLAGEFAEHIG','confirmed','MANUAL',now(),:actor
        FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"""), params)
    await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,cost_entry_id,origin,reason,reviewed_by) VALUES (:org,:property,:sphere,:cost,'REVIEWED','Scoped deadline evidence',:actor)"), params)


async def test_rental_monitor_limits_type_units_and_obligation_period(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await add_cost(ids, 'scope')
    async with client(ids) as api:
        result = await api.get('/api/fristen', params={'reference_date': '2026-12-31', 'accounting_scope_id': str(ids['scope'])})
        assert result.status_code == 200, result.text
        rows = result.json()
        bk = [row for row in rows if row['kind'] == 'BK_ABRECHNUNG']
        assert len(bk) == 1 and bk[0]['property_id'] == str(ids['property'])
        assert bk[0]['due_date'] == '2026-12-31'
        assert not any(row['kind'].startswith('WEG') or row['kind'] == 'WIRTSCHAFTSPLAN' for row in rows)
        overdue = next(row for row in rows if row['kind'] == 'OFFENE_POSTEN')
        assert overdue['detail'].startswith('24 '), overdue  # 12 rental + 12 advance months, one scoped unit
        assert (await api.get('/api/fristen', params={'reference_date': '2026-12-31'})).status_code == 409


async def test_weg_monitor_omits_tenant_debt_and_out_of_term_plan_obligations(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'owner')
    await add_cost(ids, 'scope')
    async with client(ids) as api:
        result = await api.get('/api/fristen', params={'reference_date': '2026-12-31', 'accounting_scope_id': str(ids['scope'])})
        assert result.status_code == 200, result.text
        kinds = {row['kind'] for row in result.json()}
        assert {'WEG_JAHRESABRECHNUNG', 'WEG_VERMOEGENSBERICHT'} <= kinds
        assert not kinds & {'BK_ABRECHNUNG', 'OFFENE_POSTEN', 'WIRTSCHAFTSPLAN'}
        # The second WEG remains open; it has no costs but needs a next-year plan.
        second = await api.get('/api/fristen', params={'reference_date': '2026-12-31', 'accounting_scope_id': str(ids['second_scope'])})
        assert second.status_code == 200, second.text
        assert {row['kind'] for row in second.json()} == {'WIRTSCHAFTSPLAN', 'BELEG_AUFBEWAHRUNG'}


async def test_monitor_live_scope_denial_and_invalid_reference_date(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    params = {'reference_date': '2025-12-31', 'accounting_scope_id': str(ids['scope'])}
    async with client(ids, 'actor') as admin:
        assert (await admin.get('/api/fristen', params=params)).status_code == 404
    async with client(ids) as api:
        assert (await api.get('/api/fristen', params={**params, 'reference_date': '2024-12-31'})).status_code == 422
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    async with client(ids) as api:
        assert (await api.get('/api/fristen', params=params)).status_code == 404


async def test_monitor_blocks_unclassified_inputs(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await mutate(text("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unreviewed')"), ids)
    async with client(ids) as api:
        response = await api.get('/api/fristen', params={'reference_date': '2025-12-31', 'accounting_scope_id': str(ids['scope'])})
        assert response.status_code == 409
        assert 'unclassified' in response.json()['detail']


async def test_partial_fiscal_year_requires_reviewed_coverage(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    ids['partial'] = uuid.uuid4()
    await mutate(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:partial,:org,:property,'MIET','Midyear','2025-07-01',true,'REVIEWED',:actor)"), ids)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:partial,:clerk,ARRAY['read'],:actor,'Read midyear')"), ids)
    await add_cost(ids, 'partial', '2025-07-01', '2025-12-31')
    async with client(ids) as api:
        response = await api.get('/api/fristen', params={'reference_date': '2026-12-31', 'accounting_scope_id': str(ids['partial'])})
        assert response.status_code == 409
        assert 'partial fiscal-year' in response.json()['detail']


async def test_malformed_lease_does_not_become_zero_overdue(migrator_session, scope_seed, monkeypatch):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    def invalid(*args):
        raise ValueError('Malformed calculation input')
    monkeypatch.setattr('app.services.fristen_service.tenant_expected_charges', invalid)
    async with client(ids) as api:
        response = await api.get('/api/fristen', params={'reference_date': '2025-12-31', 'accounting_scope_id': str(ids['scope'])})
        assert response.status_code == 409
        assert 'cannot calculate all lease inputs' in response.json()['detail']


async def test_partial_next_plan_year_is_not_silently_omitted(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'owner')
    ids['partial'] = uuid.uuid4()
    await mutate(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,valid_to,whole_property,origin,created_by) VALUES (:partial,:org,:property,'WEG','Partial plan year','2026-01-01','2027-06-30',true,'REVIEWED',:actor)"), ids)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:partial,:clerk,ARRAY['read'],:actor,'Read partial plan year')"), ids)
    async with client(ids) as api:
        response = await api.get('/api/fristen', params={'reference_date': '2026-12-31', 'accounting_scope_id': str(ids['partial'])})
        assert response.status_code == 409
        assert 'partial plan-year' in response.json()['detail']


async def test_unlinked_document_retention_remains_unknown_in_scoped_monitor(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    ids['document'] = uuid.uuid4()
    await mutate(text("""INSERT INTO invoice_document(id,organization_id,storage_uri,storage_backend,mime_type,
      content_byte_size,content_sha256,version,chain_root_id,status,retention_until)
      VALUES (:document,:org,'synthetic/unlinked.pdf','LOCAL_FS','application/pdf',4,:sha,1,:document,'active','2027-01-01')"""), {**ids, 'sha': 'c'*64})
    async with client(ids) as api:
        result = await api.get('/api/fristen', params={'reference_date': '2025-12-31', 'accounting_scope_id': str(ids['scope'])})
        assert result.status_code == 200, result.text
        retention = next(row for row in result.json() if row['kind'] == 'BELEG_AUFBEWAHRUNG')
        assert 'nicht verfügbar' in retention['detail']
        assert '0 Beleg' not in retention['detail']
        assert retention['due_date'] is None


@pytest.mark.parametrize('unclassified', [False, True])
async def test_context_reports_only_missing_selection_without_calculating(migrator_session, scope_seed, monkeypatch, unclassified):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    if unclassified:
        await mutate(text("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unclassified')"), ids)
    def forbidden(*args, **kwargs):
        raise AssertionError('Context must not load money or calculate deadlines')
    monkeypatch.setattr('app.services.fristen_service.FristenService._collect_property', forbidden)
    monkeypatch.setattr('app.services.fristen_service.FristenService._overdue_count', forbidden)
    async with client(ids) as api:
        response = await api.get('/api/fristen/context')
        assert response.status_code == 200, response.text
        assert response.json() == {'requires_accounting_scope': True}
        assert (await api.get('/api/fristen')).status_code == 409


async def test_context_and_monitor_keep_authorized_legacy_read(worker_tenant, monkeypatch):
    from app.services.fristen_service import FristenService
    api, seed = worker_tenant
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:actor"),
                 {'org': seed['org_a_id'], 'actor': seed['user_a_admin_id']})
    def forbidden(*args, **kwargs):
        raise AssertionError('Context must not build the monitor')
    with monkeypatch.context() as patch:
        patch.setattr(FristenService, 'build_monitor', forbidden)
        response = await api.get('/api/fristen/context')
    assert response.status_code == 200, response.text
    assert response.json() == {'requires_accounting_scope': False}
    assert (await api.get('/api/fristen')).status_code == 200


@pytest.mark.parametrize('path', ['/api/fristen/context', '/api/fristen'])
async def test_fristen_anonymous_reads_denied(client_unauth, path):
    assert (await client_unauth.get(path)).status_code == 401


@pytest.mark.parametrize('path', ['/api/fristen/context', '/api/fristen'])
@pytest.mark.parametrize('live_role,expected', [(None, 401), ('UNKNOWN', 403), ('READ_ONLY', 200)])
async def test_fristen_reads_use_current_transaction_principal(worker_tenant, path, live_role, expected):
    from app.core.tenant import AuthContext, get_current_user
    from app.main import app
    # Authentication has already returned a stale administrator; the data
    # transaction must independently validate membership and the current role.
    _, seed = worker_tenant
    ids = {'org': seed['org_a_id'], 'actor': seed['user_a_admin_id']}
    async def stale_auth():
        return AuthContext(ids['actor'], ids['org'], 'OWNER_ADMIN')
    await mutate(text('UPDATE membership SET role=:role WHERE organization_id=:org AND user_id=:actor')
                 if live_role else text('DELETE FROM membership WHERE organization_id=:org AND user_id=:actor'),
                 {**ids, 'role': live_role})
    app.dependency_overrides[get_current_user] = stale_auth
    try:
        async with client(ids, 'actor') as api:
            response = await api.get(path)
        assert response.status_code == expected, response.text
        if live_role == 'READ_ONLY':
            if path.endswith('context'):
                assert response.json() == {'requires_accounting_scope': False}
            else:
                assert isinstance(response.json(), list)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if live_role is None:
            await mutate(text("INSERT INTO membership(organization_id,user_id,role) VALUES(:org,:actor,'OWNER_ADMIN')"), ids)
        else:
            await mutate(text("UPDATE membership SET role='OWNER_ADMIN' WHERE organization_id=:org AND user_id=:actor"), ids)


async def test_context_and_selected_monitor_are_readonly_without_write_capabilities(migrator_session, scope_seed):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"), ids)
    statements = []
    def prohibit_mutation(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        assert statement.lstrip().split()[0].upper() not in {'INSERT','UPDATE','DELETE','TRUNCATE','ALTER','CREATE','DROP'}
    event.listen(Engine, 'before_cursor_execute', prohibit_mutation)
    try:
        async with client(ids) as api:
            context = await api.get('/api/fristen/context')
            assert context.status_code == 200 and context.json() == {'requires_accounting_scope': True}
            selected = await api.get('/api/fristen', params={'accounting_scope_id': str(ids['scope']), 'reference_date': '2025-12-31'})
            assert selected.status_code == 200, selected.text
        assert statements
    finally:
        event.remove(Engine, 'before_cursor_execute', prohibit_mutation)


async def test_context_does_not_bypass_hidden_property_sources(migrator_session, scope_seed):
    from tests.test_property_source_enforcement import synthetic_enforcement
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        context = await api.get('/api/fristen/context')
        monitor = await api.get('/api/fristen')
        assert context.status_code == monitor.status_code == 404
        assert context.json() == monitor.json() == {'detail': 'Complete report sources not accessible'}


async def test_foreign_scope_and_revoked_grants_do_not_gain_overview_access(migrator_session, scope_seed, seed_data):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    foreign = {**ids, 'org': seed_data['org_b_id'], 'actor': seed_data['user_b_admin_id']}
    async with client(foreign, 'actor') as api:
        assert (await api.get('/api/fristen/context')).json() == {'requires_accounting_scope': False}
        assert (await api.get('/api/fristen', params={'accounting_scope_id': str(ids['scope'])})).status_code == 404
    await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    async with client(ids) as api:
        assert (await api.get('/api/fristen/context')).json() == {'requires_accounting_scope': True}
        assert (await api.get('/api/fristen')).status_code == 409
        assert (await api.get('/api/fristen', params={'accounting_scope_id': str(ids['scope'])})).status_code == 404


@pytest.mark.parametrize('inherited', ['valid', 'hidden', 'unclassified'])
async def test_context_never_turns_inherited_database_scope_into_legacy(migrator_session, scope_seed, inherited):
    from app.core.db import get_tenant_session
    from app.services.fristen_service import FristenService
    from fastapi import HTTPException
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    if inherited == 'unclassified':
        await mutate(text("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unclassified')"), ids)
    async with get_tenant_session(ids['org']) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id',:scope,true)"),
                         {'actor':str(ids['clerk']), 'scope':str(uuid.uuid4() if inherited == 'hidden' else ids['scope'])})
        service = FristenService(db, ids['org'], ids['clerk'])
        if inherited == 'valid':
            assert await service.requires_accounting_scope() is True
        else:
            with pytest.raises(HTTPException) as denied:
                await service.requires_accounting_scope()
            assert denied.value.status_code == (404 if inherited == 'hidden' else 409)


@pytest.mark.parametrize('status', [401, 403, 404, 409, 422, 500])
async def test_context_propagates_other_overview_errors(status, monkeypatch):
    from fastapi import HTTPException
    from app.services.fristen_service import FristenService
    from app.services.financial_scope_service import FinancialScopeService
    from app.repositories.fristen_repo import FristenRepository
    async def legacy(*args):
        return None
    async def denied(*args):
        raise HTTPException(status, 'Different admission failure')
    monkeypatch.setattr(FinancialScopeService, 'select_read_scope', legacy)
    monkeypatch.setattr(FristenRepository, 'require_complete_overview', denied)
    with pytest.raises(HTTPException) as failure:
        await FristenService(None, uuid.uuid4(), uuid.uuid4()).requires_accounting_scope()
    assert failure.value.status_code == status
    assert failure.value.detail == 'Different admission failure'
