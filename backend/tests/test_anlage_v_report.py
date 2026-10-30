"""Anlage V derives from the same complete, authorized year report."""
import csv
import io
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.main import app
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_property_year_report import fixture, payment, cost, ready, query, client, snapshot
from tests.test_scoped_dunning import grant
from tests.test_scoped_account_statements import mutate

pytestmark = pytest.mark.asyncio
JSON = '/api/reports/property-year/anlage-v'
CSV = JSON + '.csv'


async def test_summary_and_csv_keep_unmapped_exclusions_and_reconcile_without_writes(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '1234.56')
    await payment(migrator_session, ids, '150.00', kind='NK_VORAUSZAHLUNG')
    await payment(migrator_session, ids, '1800.00', kind='KAUTION')
    await cost(migrator_session, ids, '100.03', code='KALTWASSER')
    await cost(migrator_session, ids, '20.00', code='ERHALTUNGSRUECKLAGE')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    before = await snapshot(ids)
    async with client(ids) as api:
        response = await api.get(JSON, params=query(ids))
        assert response.status_code == 200, response.text
        summary = response.json()
        assert (summary['property_id'], summary['year'], summary['accounting_scope_id']) == (
            str(ids['property']), 2026, str(ids['scope']))
        assert {row['code'] for row in summary['unmapped']} == {'KAUTION', 'ERHALTUNGSRUECKLAGE'}
        assert [(row['mapped_total'], row['unmapped_total'], row['report_total'], row['difference'])
                for row in summary['reconciliation']] == [
                    ('1384.56', '1800.00', '3184.56', '0.00'), ('100.03', '20.00', '120.03', '0.00')]
        response = await api.get(CSV, params=query(ids))
        assert response.status_code == 200, response.text
        assert response.content.startswith(b'\xef\xbb\xbf')
        assert b'\n' not in response.content.replace(b'\r\n', b'')
        assert response.headers['cache-control'] == 'private, no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['content-type'] == 'text/csv; charset=utf-8'
        assert 'anlage-v-2026.csv' in response.headers['content-disposition']
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        assert rows[0] == ['Objekt', 'Jahr', 'Bereich', 'Zuordnung', 'Bezeichnung', 'Hinweis', 'Betrag (EUR)']
        for row in summary['lines']:
            assert ['Jahresobjekt', '2026', row['section'], 'Zugeordnet', row['label'], row['note'], row['amount'].replace('.', ',')] in rows
        for row in summary['unmapped']:
            assert ['Jahresobjekt', '2026', row['section'], 'Nicht zugeordnet', f"{row['label']} ({row['code']})", row['note'], row['amount'].replace('.', ',')] in rows
        assert any('Zuordnungshilfe, keine Steuerberatung' in field for row in rows for field in row)
    assert await snapshot(ids) == before


@pytest.mark.parametrize('path', [JSON, CSV])
async def test_foreign_unknown_and_unauthenticated_requests_keep_report_guards(migrator_session, scope_seed, seed_data, path):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    foreign = {'org': seed_data['org_b_id'], 'clerk': seed_data['user_b_admin_id']}
    async with client(foreign) as api:
        assert (await api.get(path, params=query(ids))).status_code == 404
        assert (await api.get(path, params=query(ids, property_id=str(uuid4()), accounting_scope_id=str(uuid4())))).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
        assert (await api.get(path, params=query(ids))).status_code == 401
    async with client(ids) as api:
        assert (await api.get(path, params=query(ids, year=0))).status_code == 422
        assert (await api.get(path, params=query(ids, accounting_scope_id=str(uuid4())))).status_code == 404
        assert (await api.get(path, params={'property_id': str(ids['property']), 'year': 2026})).status_code == 409


async def test_read_access_is_enough_for_summary_but_not_csv(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"), ids)
    async with client(ids) as api:
        assert (await api.get(JSON, params=query(ids))).status_code == 200
        assert (await api.get(CSV, params=query(ids))).status_code == 404


@pytest.mark.parametrize('path', [JSON, CSV])
@pytest.mark.parametrize('change,expected', [('scope', 404), ('user', 401), ('unclassified', 409)])
async def test_live_authority_and_complete_sources_are_reused(migrator_session, scope_seed, path, change, expected):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids) as api:
        assert (await api.get(path, params=query(ids))).status_code == 200
        statements = {
            'scope': "UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL",
            'user': 'UPDATE "user" SET is_active=false WHERE id=:clerk',
            'unclassified': "INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unclassified')",
        }
        await mutate(text(statements[change]), ids)
        assert (await api.get(path, params=query(ids))).status_code == expected


@pytest.mark.parametrize('source', ['property', 'contact'])
async def test_source_read_permissions_do_not_imply_export(migrator_session, scope_seed, source):
    from tests.test_report_source_access import property_for_clerk
    from tests.contact_source_fixtures import grant_contact
    from tests.test_property_source_enforcement import synthetic_enforcement

    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '700')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids, 'actor') as admin:
        await grant_contact(admin, {**scope_seed, 'user_a_admin_id': ids['clerk']}, ids['person'],
                            caps=('read',) if source == 'contact' else ('read', 'export'))
    await property_for_clerk(ids, scope_seed, caps=('read',) if source == 'property' else ('read', 'export'))
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        assert (await api.get(JSON, params=query(ids))).status_code == 200
        assert (await api.get(CSV, params=query(ids))).status_code == 404


@pytest.mark.parametrize('guard', ['scope', 'property_financial_disclosure_access', 'report_scope_source_access'])
async def test_csv_rechecks_all_export_guards_after_rendering(migrator_session, scope_seed, monkeypatch, guard):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.api import reports
    from app.core.rbac import ScopeCapability
    from app.services.authorization_service import AuthorizationService

    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    rendered = False
    original_writer, original_scalar, original_require = csv.writer, AsyncSession.scalar, AuthorizationService.require

    def writer(*args, **kwargs):
        nonlocal rendered
        rendered = True
        return original_writer(*args, **kwargs)

    async def scalar(self, statement, *args, **kwargs):
        if rendered and guard in str(statement) and "'export'" in str(statement):
            return False
        return await original_scalar(self, statement, *args, **kwargs)

    async def require(self, scope_id, capability):
        if rendered and guard == 'scope' and capability == ScopeCapability.EXPORT:
            raise HTTPException(404, 'Export revoked during rendering')
        return await original_require(self, scope_id, capability)

    monkeypatch.setattr(reports.csv, 'writer', writer)
    monkeypatch.setattr(AsyncSession, 'scalar', scalar)
    monkeypatch.setattr(AuthorizationService, 'require', require)
    async with client(ids) as api:
        result = await api.get(CSV, params=query(ids))
    assert rendered
    assert result.status_code == 404
    assert not result.content.startswith(b'\xef\xbb\xbf')


async def test_unmapped_csv_text_is_formula_safe_and_negative_money_stays_numeric():
    from app.api.reports import _render_anlage_v_csv
    from app.engine.anlage_v import map_property_year
    from tests.engine.test_anlage_v import year_report

    report = year_report(income=[('=UNKNOWN', '-0.01')], expenses=[('NEW', '-12.34')])
    report.property_name = '  =1+1'
    report.expenses[0].category_name = '@SUM(A1)'
    rows = list(csv.reader(io.StringIO(_render_anlage_v_csv(map_property_year(report)).decode('utf-8-sig')), delimiter=';'))
    assert all(row[0] == "'  =1+1" for row in rows[1:])
    assert any(row[4] == "'=UNKNOWN (=UNKNOWN)" and row[-1] == '-0,01' for row in rows)
    assert any(row[4] == "'@SUM(A1) (NEW)" and row[-1] == '-12,34' for row in rows)
