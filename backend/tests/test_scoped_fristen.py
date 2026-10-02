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
