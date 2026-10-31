"""CSV is an exact fresh object-year report with additional live export authority."""
import csv
import io
import uuid
from decimal import localcontext
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import text

from app.core.tenant import AuthContext, get_current_user
from app.main import app
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_property_year_report import ANLAGE_V, fixture, payment, cost, ready, query, client, report, snapshot
from tests.test_scoped_account_statements import mutate
from tests.test_scoped_dunning import grant
from tests.test_report_source_access import property_for_clerk
from tests.contact_source_fixtures import grant_contact
from tests.test_property_source_enforcement import synthetic_enforcement

pytestmark = pytest.mark.asyncio
HEADER = ['Objekt', 'Jahr', 'Bereich', 'Zahlungstyp', 'Kategorie', 'Umlagefähigkeit', 'Betrag (EUR)']
ANLAGE_V_CSV = ANLAGE_V + '.csv'


async def export(api, ids, **overrides):
    return await api.get('/api/reports/property-year.csv', params=query(ids, **overrides))


def rows(response):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b'\xef\xbb\xbf')
    return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'), newline=''), delimiter=';'))


async def test_exact_csv_contract_json_parity_and_no_writes(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    for amount, direction in [('700', 'IN'), ('150', 'IN'), ('50', 'OUT')]:
        await payment(migrator_session, ids, amount, direction=direction)
    _, category = await cost(migrator_session, ids, '100')
    await cost(migrator_session, ids, '25', apportion='NICHT_UMLAGEFAEHIG')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    before = await snapshot(ids)
    async with client(ids) as api:
        body = (await report(api, ids)).json()
        result = await export(api, ids)
        assert rows(result) == [HEADER,
            ['Jahresobjekt', '2026', 'Einnahmen', 'KALTMIETE', '', '', '800,00'],
            ['Jahresobjekt', '2026', 'Ausgaben', '', category.label, 'NICHT_UMLAGEFAEHIG', '25,00'],
            ['Jahresobjekt', '2026', 'Ausgaben', '', category.label, 'UMLAGEFAEHIG', '100,00'],
            ['Jahresobjekt', '2026', 'Einnahmen gesamt', '', '', '', '800,00'],
            ['Jahresobjekt', '2026', 'Ausgaben gesamt', '', '', '', '125,00'],
            ['Jahresobjekt', '2026', 'Saldo', '', '', '', '675,00']]
        assert [r[-1] for r in rows(result)[-3:]] == [body[k].replace('.', ',') for k in ('income_total', 'expense_total', 'balance')]
        assert result.headers['content-type'] == 'text/csv; charset=utf-8'
        assert result.headers['x-content-type-options'] == 'nosniff'
        assert result.headers['cache-control'] == 'private, no-store'
        assert result.headers['content-disposition'] == ('attachment; filename="objekt-jahresuebersicht-2026.csv"; '
            "filename*=UTF-8''objekt-jahresuebersicht-2026.csv")
        assert result.content.count(b'\r\n') == 7
        assert b'\n' not in result.content.replace(b'\r\n', b'')
    assert await snapshot(ids) == before


@pytest.mark.parametrize('name', ['=1+1', '+SUM(A1)', '-1+2', '@SUM(A1)', '\t=1', '\r=1', '\n=1', '  =1', 'Müller; "Hof"\r\nSüd'])
async def test_text_escaping_formula_protection_and_negative_cent(migrator_session, scope_seed, monkeypatch, name):
    from app.services.report_service import ReportService
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await migrator_session.execute(text('UPDATE property SET name=:name WHERE id=:property'), {**ids, 'name': name})
    await payment(migrator_session, ids, '0.01', direction='OUT')
    await cost(migrator_session, ids, '-0.01')
    await ready(migrator_session, ids)
    original = ReportService.property_year

    async def named_category(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        result.expenses[0].category_name = name
        return result

    monkeypatch.setattr(ReportService, 'property_year', named_category)
    async with client(ids) as api:
        result = await export(api, ids)
    data = rows(result)
    safe = name if name.startswith('Müller') else "'" + name
    assert data[1] == [safe, '2026', 'Einnahmen', 'KALTMIETE', '', '', '-0,01']
    assert data[2] == [safe, '2026', 'Ausgaben', '', safe, 'UMLAGEFAEHIG', '-0,01']
    assert data[-1][-1] == '0,00'
    assert 'objekt-jahresuebersicht-2026.csv' in result.headers['content-disposition']
    assert name not in result.headers['content-disposition']


async def test_low_precision_exact_large_money_and_date_basis(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await payment(migrator_session, ids, '1234567.89', day='2026-01-01')
    await payment(migrator_session, ids, '0.02', kind='NK_VORAUSZAHLUNG', day='2026-12-31')
    await cost(migrator_session, ids, '0.01')
    for day in ['2025-12-31', '2027-01-01']:
        await payment(migrator_session, ids, '999', day=day)
        await cost(migrator_session, ids, '999', day=day)
    await payment(migrator_session, ids, '999', status='draft')
    await cost(migrator_session, ids, '999', status='needs_review')
    await ready(migrator_session, ids)
    async with client(ids) as api:
        with localcontext() as context:
            context.prec = 6
            result = await export(api, ids)
    assert [r[-1] for r in rows(result)[-3:]] == ['1234567,91', '0,01', '1234567,90']


@pytest.mark.parametrize('override', [{'year': 0}, {'year': 10000}, {'year': 'bad'}, {'property_id': 'bad'}, {'accounting_scope_id': 'bad'}])
async def test_same_query_validation(migrator_session, scope_seed, override):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        assert (await export(api, ids, **override)).status_code == (await report(api, ids, **override)).status_code == 422


@pytest.mark.parametrize('options', [dict(start='2026-02-01'), dict(end='2026-11-30'), dict(whole=False),
    dict(management_type='WEG', scope_type='MIET'), dict(management_type='MIET', scope_type='WEG'), dict(scope_type='SEV', whole=False)])
async def test_same_whole_object_scope_guards(migrator_session, scope_seed, options):
    ids = await fixture(migrator_session, scope_seed, **options)
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids) as api:
        assert (await export(api, ids)).status_code == (await report(api, ids)).status_code == 422


@pytest.mark.parametrize('year', [1, 9999])
async def test_empty_boundary_years(migrator_session, scope_seed, year):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        data = rows(await export(api, ids, year=year))
    assert data == [HEADER] + [['Jahresobjekt', str(year), label, '', '', '', '0,00']
        for label in ['Einnahmen gesamt', 'Ausgaben gesamt', 'Saldo']]


async def test_read_not_export_no_admin_fallback_and_org_auth(migrator_session, scope_seed, seed_data):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        assert (await report(api, ids)).status_code == 200
        assert (await export(api, ids)).status_code == 404
        omitted = query(ids)
        del omitted['accounting_scope_id']
        assert (await api.get('/api/reports/property-year.csv', params=omitted)).status_code == 409
    async with client(ids, 'actor') as api:
        assert (await export(api, ids)).status_code == 404
    foreign = {'org': seed_data['org_b_id'], 'clerk': seed_data['user_b_admin_id']}
    async with client(foreign) as api:
        assert (await export(api, ids)).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
        assert (await export(api, ids)).status_code == 401


@pytest.mark.parametrize('change,expected', [('scope', 404), ('member', 404), ('user', 401), ('unclassified', 409)])
async def test_live_export_roles_and_complete_inputs(migrator_session, scope_seed, change, expected):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids) as api:
        assert (await export(api, ids)).status_code == 200
        statements = {
            'scope': "UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL",
            'member': "UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk",
            'user': 'UPDATE "user" SET is_active=false WHERE id=:clerk',
            'unclassified': "INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unclassified')"}
        await mutate(text(statements[change]), ids)
        assert (await export(api, ids)).status_code == expected


async def test_live_member_after_authentication(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    await mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'), ids)

    async def authenticated():
        return AuthContext(ids['clerk'], ids['org'], 'CLERK')

    app.dependency_overrides[get_current_user] = authenticated
    try:
        async with client(ids) as api:
            assert (await export(api, ids)).status_code == 401
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize('source', ['property', 'contact'])
async def test_source_read_not_export_and_export_revocation(migrator_session, scope_seed, source):
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '700')
    await cost(migrator_session, ids, '100')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids, 'actor') as admin:
        contact_domain, contact_receipt = await grant_contact(admin,
            {**scope_seed, 'user_a_admin_id': ids['clerk']}, ids['person'], caps=('read',) if source == 'contact' else ('read', 'export'))
    domain, receipt = await property_for_clerk(ids, scope_seed, caps=('read',) if source == 'property' else ('read', 'export'))
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        assert (await report(api, ids)).status_code == 200
        assert (await export(api, ids)).status_code == 404
        async with client(ids, 'actor') as admin:
            prefix, selected_domain, selected_receipt = ('property', domain, receipt) if source == 'property' else ('person', contact_domain, contact_receipt)
            base = f'/api/{prefix}-data-domains/{selected_domain}/grants'
            assert (await admin.post(f'{base}/{selected_receipt}/revoke')).status_code == 204
            body = {'user_id': str(ids['clerk']), 'capabilities': ['read', 'export']}
            body.update({'reason': 'CSV export'} if source == 'property' else {'reason_code': 'TEAM_ACCESS'})
            allowed = await admin.post(base, headers={'Idempotency-Key': str(uuid.uuid4())}, json=body)
            assert allowed.status_code == 201, allowed.text
            new_receipt = allowed.json()['id']
        assert rows(await export(api, ids))[-1][-1] == '600,00'
        async with client(ids, 'actor') as admin:
            assert (await admin.post(f'{base}/{new_receipt}/revoke')).status_code == 204
        assert (await export(api, ids)).status_code == 404


async def test_export_rechecked_after_rendering(migrator_session, scope_seed, monkeypatch):
    from app.services.authorization_service import AuthorizationService
    from app.core.rbac import ScopeCapability
    from fastapi import HTTPException
    from app.api import reports
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    rendered = False
    original_require = AuthorizationService.require

    async def changed_authority(self, scope_id, capability):
        if rendered and capability == ScopeCapability.EXPORT:
            raise HTTPException(404, 'Export revoked during rendering')
        return await original_require(self, scope_id, capability)

    # Patch the standard writer, not a private implementation helper.
    original_writer = csv.writer

    def writer(*args, **kwargs):
        nonlocal rendered
        rendered = True
        return original_writer(*args, **kwargs)

    monkeypatch.setattr(AuthorizationService, 'require', changed_authority)
    with patch.object(reports, 'csv', csv, create=True), patch.object(csv, 'writer', writer):
        async with client(ids) as api:
            assert (await export(api, ids)).status_code == 404
    assert rendered


async def test_fresh_report_after_new_booked_payment(migrator_session, scope_seed):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from tests.conftest import MIGRATOR_URL
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await payment(migrator_session, ids, '10.01')
    await ready(migrator_session, ids)
    async with client(ids) as api:
        assert rows(await export(api, ids))[-1][-1] == '10,01'
        engine = create_async_engine(MIGRATOR_URL)
        try:
            async with AsyncSession(engine) as session, session.begin():
                await payment(session, ids, '0.02')
        finally:
            await engine.dispose()
        result = await export(api, ids)
        assert rows(result)[-1][-1] == '10,03'
        assert (await report(api, ids)).json()['balance'] == '10.03'


@pytest.mark.parametrize('value', ['=1+1', '+1.00', '1e2', '1,00', '01.00', '1.0', '1.001',
    'NaN', 'Infinity', '1.00\r\n=1', '１２.00', 1.25, None])
async def test_noncanonical_money_is_never_serialized(value):
    from app.api.reports import _csv_money
    with pytest.raises(ValueError, match='canonical decimal string'):
        _csv_money(value)


@pytest.mark.parametrize('role', ['CLERK', 'OWNER_ADMIN'])
async def test_live_writer_roles_still_require_explicit_scope_export(migrator_session, scope_seed, role):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await mutate(text('UPDATE membership SET role=:role WHERE organization_id=:org AND user_id=:clerk'),
        {**ids, 'role': role})
    async with client(ids) as api:
        assert (await export(api, ids)).status_code == 404
        await grant(ids, ['read', 'export'])
        assert rows(await export(api, ids))[-1][-1] == '0,00'


@pytest.mark.parametrize('guard', ['property_financial_disclosure_access', 'report_scope_source_access'])
async def test_source_export_rechecked_after_rendering(migrator_session, scope_seed, monkeypatch, guard):
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.api import reports
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    original_render = reports._render_property_year_csv
    original_scalar = AsyncSession.scalar
    rendered = False
    denied = False

    def render(body):
        nonlocal rendered
        content = original_render(body)
        rendered = True
        return content

    async def scalar(self, statement, *args, **kwargs):
        nonlocal denied
        if rendered and guard in str(statement) and "'export'" in str(statement):
            denied = True
            return False
        return await original_scalar(self, statement, *args, **kwargs)

    monkeypatch.setattr(reports, '_render_property_year_csv', render)
    monkeypatch.setattr(AsyncSession, 'scalar', scalar)
    async with client(ids) as api:
        response = await export(api, ids)
    assert rendered and denied
    assert response.status_code == 404
    assert not response.content.startswith(b'\xef\xbb\xbf')


async def test_expired_scope_export_grant(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    await migrator_session.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor "
        "WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL"), ids)
    await migrator_session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,"
        "granted_by,reason,created_at,expires_at) VALUES (:org,:scope,:clerk,ARRAY['read','export'],"
        ":actor,'Expired export',now()-interval '2 days',now()-interval '1 day')"), ids)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        assert (await export(api, ids)).status_code == 404


async def test_anlage_v_csv_matches_summary_and_reconciles_without_writes(migrator_session, scope_seed):
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
        response = await api.get(ANLAGE_V_CSV, params=query(ids))
        data = rows(response)
        assert b'\n' not in response.content.replace(b'\r\n', b'')
        assert response.headers['cache-control'] == 'private, no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['content-type'] == 'text/csv; charset=utf-8'
        assert 'anlage-v-2026.csv' in response.headers['content-disposition']
        assert data[0] == ['Objekt', 'Jahr', 'Bereich', 'Zuordnung', 'Bezeichnung', 'Hinweis', 'Betrag (EUR)']
        response = await api.get(ANLAGE_V, params=query(ids))
        assert response.status_code == 200, response.text
        summary = response.json()
        for row in summary['lines']:
            assert ['Jahresobjekt', '2026', row['section'], 'Zugeordnet', row['label'], row['note'], row['amount'].replace('.', ',')] in data
        for row in summary['unmapped']:
            assert ['Jahresobjekt', '2026', row['section'], 'Nicht zugeordnet', f"{row['label']} ({row['code']})", row['note'], row['amount'].replace('.', ',')] in data
        assert any('Zuordnungshilfe, keine Steuerberatung' in field for row in data for field in row)
    assert await snapshot(ids) == before


async def test_anlage_v_csv_foreign_unknown_and_unauthenticated_requests_keep_report_guards(migrator_session, scope_seed, seed_data):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    foreign = {'org': seed_data['org_b_id'], 'clerk': seed_data['user_b_admin_id']}
    async with client(foreign) as api:
        assert (await api.get(ANLAGE_V_CSV, params=query(ids))).status_code == 404
        assert (await api.get(ANLAGE_V_CSV, params=query(ids, property_id=str(uuid.uuid4()), accounting_scope_id=str(uuid.uuid4())))).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
        assert (await api.get(ANLAGE_V_CSV, params=query(ids))).status_code == 401
    async with client(ids) as api:
        assert (await api.get(ANLAGE_V_CSV, params=query(ids, year=0))).status_code == 422
        assert (await api.get(ANLAGE_V_CSV, params=query(ids, accounting_scope_id=str(uuid.uuid4())))).status_code == 404
        assert (await api.get(ANLAGE_V_CSV, params={'property_id': str(ids['property']), 'year': 2026})).status_code == 409


async def test_read_access_is_enough_for_anlage_v_summary_but_not_csv(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"), ids)
    async with client(ids) as api:
        assert (await api.get(ANLAGE_V, params=query(ids))).status_code == 200
        assert (await api.get(ANLAGE_V_CSV, params=query(ids))).status_code == 404


@pytest.mark.parametrize('change,expected', [('scope', 404), ('user', 401), ('unclassified', 409)])
async def test_anlage_v_csv_reuses_live_authority_and_complete_sources(migrator_session, scope_seed, change, expected):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids) as api:
        assert (await api.get(ANLAGE_V_CSV, params=query(ids))).status_code == 200
        statements = {
            'scope': "UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL",
            'user': 'UPDATE "user" SET is_active=false WHERE id=:clerk',
            'unclassified': "INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unclassified')",
        }
        await mutate(text(statements[change]), ids)
        assert (await api.get(ANLAGE_V_CSV, params=query(ids))).status_code == expected


@pytest.mark.parametrize('source', ['property', 'contact'])
async def test_anlage_v_source_read_permissions_do_not_imply_export(migrator_session, scope_seed, source):
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '700')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids, 'actor') as admin:
        await grant_contact(admin, {**scope_seed, 'user_a_admin_id': ids['clerk']}, ids['person'],
                            caps=('read',) if source == 'contact' else ('read', 'export'))
    await property_for_clerk(ids, scope_seed, caps=('read',) if source == 'property' else ('read', 'export'))
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        assert (await api.get(ANLAGE_V, params=query(ids))).status_code == 200
        assert (await api.get(ANLAGE_V_CSV, params=query(ids))).status_code == 404


@pytest.mark.parametrize('guard', ['scope', 'property_financial_disclosure_access', 'report_scope_source_access'])
async def test_anlage_v_csv_rechecks_all_export_guards_after_rendering(migrator_session, scope_seed, monkeypatch, guard):
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
        result = await api.get(ANLAGE_V_CSV, params=query(ids))
    assert rendered
    assert result.status_code == 404
    assert not result.content.startswith(b'\xef\xbb\xbf')


async def test_unmapped_anlage_v_csv_text_is_formula_safe_and_negative_money_stays_numeric():
    from app.api.reports import _render_anlage_v_csv
    from app.engine.anlage_v import map_property_year
    from tests.engine.test_anlage_v import year_report

    report = year_report(income=[('=UNKNOWN', '-0.01')], expenses=[('NEW', '-12.34')])
    report.property_name = '  =1+1'
    report.expenses[0].category_name = '@SUM(A1)'
    data = list(csv.reader(io.StringIO(_render_anlage_v_csv(map_property_year(report)).decode('utf-8-sig')), delimiter=';'))
    assert all(row[0] == "'  =1+1" for row in data[1:])
    assert any(row[4] == "'=UNKNOWN (=UNKNOWN)" and row[-1] == '-0,01' for row in data)
    assert any(row[4] == "'@SUM(A1) (NEW)" and row[-1] == '-12,34' for row in data)
