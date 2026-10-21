"""CSV list exports keep read visibility and require additional live export authority."""
import csv
import io
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_property_year_report import fixture, cost, ready, client, snapshot

pytestmark = pytest.mark.asyncio


async def payment(session, ids, amount, *, kind='KALTMIETE', direction='IN',
                  day='2026-06-01', status='booked', note=None,
                  tenant_profile_id=None, owner_profile_id=None, lease_agreement_id=None,
                  period_year=2025, period_month=1, cancelled_at=None, reverses=None):
    key = uuid.uuid4()
    await session.execute(text("""INSERT INTO payment(id,organization_id,property_id,bank_account_id,
        amount,direction,value_date,payment_type,tenant_profile_id,owner_profile_id,
        lease_agreement_id,period_year,period_month,status,note,cancelled_at,reverses_id)
        VALUES (:id,:org,:property,:bank,:amount,:direction,CAST(:day AS date),:kind,:tenant_id,
        :owner_id,:lease,:year,:month,:status,:note,:cancelled_at,:reverses)"""),
        {**ids, 'id': key, 'amount': Decimal(amount), 'direction': direction, 'day': date.fromisoformat(day),
         'kind': kind, 'status': status, 'note': note, 'lease': lease_agreement_id,
         'year': period_year, 'month': period_month, 'cancelled_at': cancelled_at, 'reverses': reverses,
         'tenant_id': tenant_profile_id or (ids['tenant'] if kind in ('KALTMIETE','NK_VORAUSZAHLUNG','KAUTION','NACHZAHLUNG') else None),
         'owner_id': owner_profile_id or (ids['owner'] if kind in ('HAUSGELD_VORSCHUSS','RUECKLAGENANTEIL','SONDERZAHLUNG') else None)})
    ids['payments'].append(key)
    return key


@pytest.mark.parametrize('path', ['payments', 'cost-entries'])
async def test_csv_route_precedes_uuid_catchall(migrator_session, scope_seed, path):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        response = await api.get(f'/api/{path}/export.csv')
    assert response.status_code == 200, response.text
    assert response.content.startswith(b'\xef\xbb\xbf')


async def test_cost_list_combines_status_and_property(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    other = await fixture(migrator_session, scope_seed, bound=False)
    expected, _ = await cost(migrator_session, ids, '12.34', status='needs_review')
    await cost(migrator_session, other, '56.78', status='needs_review')
    await cost(migrator_session, ids, '90.00', status='rejected')
    await ready(migrator_session, ids)
    async with client(ids, 'actor') as api:
        response = await api.get('/api/cost-entries', params={'status': 'needs_review', 'property_id': str(ids['property'])})
    assert response.status_code == 200, response.text
    assert [row['id'] for row in response.json()] == [str(expected)]

PAYMENT_HEADER = ['ID', 'Objekt-ID', 'Konto-ID', 'Wertstellung', 'Richtung', 'Zahlungsart',
    'Betrag (EUR)', 'Saldo-Betrag (EUR)', 'Status', 'Periodenjahr', 'Periodenmonat',
    'Mieter-ID', 'Eigentümer-ID', 'Vertrag-ID', 'Notiz']
COST_HEADER = ['ID', 'Objekt-ID', 'Rechnungsdatum', 'Kategorie-ID', 'Kategorieversion',
    'Brutto (EUR)', 'Umlagefähigkeit', 'Status', 'Belegnummer', 'Ersetzt durch-ID']


def rows(response):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b'\xef\xbb\xbf')
    return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'), newline=''), delimiter=';'))


@pytest.mark.parametrize('path,header,filename', [('payments', PAYMENT_HEADER, 'zahlungen.csv'),
    ('cost-entries', COST_HEADER, 'kosten.csv')])
async def test_empty_headers_and_explicit_inaccessible_parent(migrator_session, scope_seed, path, header, filename):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        response = await api.get(f'/api/{path}/export.csv')
        assert rows(response) == [header]
        assert response.content.endswith(b'\r\n')
        assert response.headers['content-type'] == 'text/csv; charset=utf-8'
        assert response.headers['cache-control'] == 'private, no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['content-disposition'] == f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}'
        filters = ['bank_account_id', 'lease_agreement_id', 'tenant_profile_id', 'owner_profile_id'] if path == 'payments' else ['property_id']
        for key in filters:
            denied = await api.get(f'/api/{path}/export.csv', params={key: str(uuid.uuid4())})
            assert denied.status_code == 404, denied.text
            assert 'text/csv' not in denied.headers['content-type']


async def test_payment_all_seven_filters_and_conjunction(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    ids.update(unit=uuid.uuid4(), lease=uuid.uuid4())
    await migrator_session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit,:org,:property,'1','WOHNUNG')"), ids)
    await migrator_session.execute(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,deposit,notes) VALUES (:lease,:org,:unit,:tenant,'2025-01-01',700,100,0,'')"), ids)
    for amount, kind, status, year in [('200', 'KALTMIETE', 'booked', 2026), ('20', 'HAUSGELD_VORSCHUSS', 'draft', 2025)]:
        key = await payment(migrator_session, ids, amount, kind=kind, status=status,
            lease_agreement_id=ids['lease'] if kind == 'KALTMIETE' else None, period_year=year, period_month=6)
    await ready(migrator_session, ids)
    filters = dict(bank_account_id=str(ids['bank']), lease_agreement_id=str(ids['lease']),
        tenant_profile_id=str(ids['tenant']), owner_profile_id=str(ids['owner']),
        payment_type='KALTMIETE', period_year=2026, status='booked')
    async with client(ids) as api:
        for query in [{}, *[{key: value} for key, value in filters.items()], filters,
                      {**filters, 'payment_type': 'SONSTIGE'}, {'status': 'draft'}, {'status': 'cancelled'}]:
            listed = await api.get('/api/payments', params=query)
            assert listed.status_code == 200, listed.text
            data = rows(await api.get('/api/payments/export.csv', params=query))
            assert data[0] == PAYMENT_HEADER
            assert [row[0] for row in data[1:]] == [row['id'] for row in listed.json()]


@pytest.mark.parametrize('value', ['=1+1', '  +SUM(A1)', '-1+2', '@SUM(A1)', '\t=1', '\r=1', '\n=1', 'Müller; "Hof"\r\nSüd', pytest.param('ä' * 500, id='long-note')])
async def test_money_text_utf8_newlines_and_no_writes(migrator_session, scope_seed, value):
    from decimal import localcontext
    ids = await fixture(migrator_session, scope_seed, bound=False)
    incoming = await payment(migrator_session, ids, '200', note=value, status='draft')
    outgoing = await payment(migrator_session, ids, '20', direction='OUT')
    large = await payment(migrator_session, ids, '123456789012.99', direction='OUT')
    entry, _ = await cost(migrator_session, ids, '-0.01', status='rejected')
    await migrator_session.execute(text('UPDATE cost_entry SET document_number=:value WHERE id=:id'), {'id': entry, 'value': value[:100]})
    await ready(migrator_session, ids)
    before = await snapshot(ids)
    async with client(ids) as api:
        with localcontext() as context:
            context.prec = 6
            payments = rows(await api.get('/api/payments/export.csv'))
            costs = rows(await api.get('/api/cost-entries/export.csv'))
    by_id = {row[0]: row for row in payments[1:]}
    assert by_id[str(incoming)][6:9] == ['200,00', '200,00', 'draft']
    assert by_id[str(outgoing)][6:8] == ['20,00', '-20,00']
    assert by_id[str(large)][6:8] == ['123456789012,99', '-123456789012,99']
    assert Decimal(by_id[str(incoming)][7].replace(',', '.')) + Decimal(by_id[str(outgoing)][7].replace(',', '.')) == 180
    safe = value if value.startswith(('Müller', 'ä')) else "'" + value
    assert by_id[str(incoming)][-1] == safe
    assert costs[1][5] == '-0,01'
    assert costs[1][8] == (value[:100] if value.startswith(('Müller', 'ä')) else "'" + value[:100])
    assert await snapshot(ids) == before


@pytest.mark.parametrize('path', ['payments', 'cost-entries'])
async def test_export_scope_is_additional_and_never_partial(migrator_session, scope_seed, path):
    from tests.test_scoped_dunning import grant
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '200')
    await cost(migrator_session, ids, '20')
    other = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, other, '300')
    await cost(migrator_session, other, '30')
    from tests.test_scope_grants import bind
    await bind(migrator_session, other)
    for kind, keys in [('payment', other['payments']), ('cost_entry', other['costs'])]:
        for key in keys:
            column = ',payment_bank_account_id' if kind == 'payment' else ''
            value = ',:bank' if kind == 'payment' else ''
            await migrator_session.execute(text(f"INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,{kind}_id{column},origin,reason,reviewed_by) VALUES (:org,:property,:scope,:id{value},'REVIEWED','CSV fixture',:actor)"), {**other, 'id': key})
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids) as api:
        assert len((await api.get(f'/api/{path}')).json()) == 2
        assert (await api.get(f'/api/{path}/export.csv')).status_code == 404
        await grant(other, ['read', 'export'])
        assert len(rows(await api.get(f'/api/{path}/export.csv'))) == 3
    async with client(ids, 'actor') as api:
        assert rows(await api.get(f'/api/{path}/export.csv')) == [PAYMENT_HEADER if path == 'payments' else COST_HEADER]


@pytest.mark.parametrize('path', ['payments', 'cost-entries'])
async def test_recheck_after_render(migrator_session, scope_seed, monkeypatch, path):
    from app.core.rbac import ScopeCapability
    from app.services.authorization_service import AuthorizationService
    from fastapi import HTTPException
    from app.api import payments, cost_entries
    from tests.test_scoped_dunning import grant
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '1')
    await cost(migrator_session, ids, '1')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    module = payments if path == 'payments' else cost_entries
    name = '_render_payments_csv' if path == 'payments' else '_render_cost_entries_csv'
    renderer = getattr(module, name)
    original = AuthorizationService.require
    rendered = False
    def render(*args):
        nonlocal rendered
        result = renderer(*args)
        rendered = True
        return result
    async def require(self, scope, capability):
        if rendered and capability == ScopeCapability.EXPORT:
            raise HTTPException(404, 'Expired after render')
        return await original(self, scope, capability)
    monkeypatch.setattr(module, name, render)
    monkeypatch.setattr(AuthorizationService, 'require', require)
    async with client(ids) as api:
        result = await api.get(f'/api/{path}/export.csv')
    assert rendered and result.status_code == 404
    assert 'text/csv' not in result.headers['content-type']


@pytest.mark.parametrize('path', ['payments', 'cost-entries'])
@pytest.mark.parametrize('source', ['property', 'person'])
async def test_source_read_export_and_revocation(migrator_session, scope_seed, path, source):
    from tests.test_scoped_dunning import grant
    from tests.test_report_source_access import property_for_clerk
    from tests.contact_source_fixtures import grant_contact
    from tests.test_property_source_enforcement import synthetic_enforcement
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '1')
    entry, _ = await cost(migrator_session, ids, '1', status='needs_review')
    if path == 'cost-entries':
        ids['vendor'] = uuid.uuid4()
        await migrator_session.execute(text("INSERT INTO vendor(id,organization_id,person_id,notes) VALUES (:vendor,:org,:person,'')"), ids)
        await migrator_session.execute(text('UPDATE cost_entry SET vendor_id=:vendor WHERE id=:entry'), {**ids, 'entry': entry})
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids, 'actor') as admin:
        contact_domain, contact_receipt = await grant_contact(admin,
            {**scope_seed, 'user_a_admin_id': ids['clerk']}, ids['person'],
            caps=('read',) if source == 'person' else ('read', 'export'))
    domain, receipt = await property_for_clerk(ids, scope_seed,
        caps=('read',) if source == 'property' else ('read', 'export'))
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        assert len((await api.get(f'/api/{path}')).json()) == 1
        assert (await api.get(f'/api/{path}/export.csv')).status_code == 404
        prefix, domain, receipt = ('property', domain, receipt) if source == 'property' else ('person', contact_domain, contact_receipt)
        async with client(ids, 'actor') as admin:
            base = f'/api/{prefix}-data-domains/{domain}/grants'
            assert (await admin.post(f'{base}/{receipt}/revoke')).status_code == 204
            body = {'user_id': str(ids['clerk']), 'capabilities': ['read', 'export']}
            body.update({'reason': 'CSV export'} if source == 'property' else {'reason_code': 'TEAM_ACCESS'})
            result = await admin.post(base, headers={'Idempotency-Key': str(uuid.uuid4())}, json=body)
            assert result.status_code == 201, result.text
            receipt = result.json()['id']
        assert len(rows(await api.get(f'/api/{path}/export.csv'))) == 2
        async with client(ids, 'actor') as admin:
            assert (await admin.post(f'{base}/{receipt}/revoke')).status_code == 204
        assert (await api.get(f'/api/{path}/export.csv')).status_code == 404


@pytest.mark.parametrize('path', ['payments', 'cost-entries'])
@pytest.mark.parametrize('change,expected', [('readonly', 404), ('deactivated', 401), ('revoked', 200)])
async def test_live_roles_revocation_and_org(migrator_session, scope_seed, seed_data, path, change, expected):
    from tests.test_scoped_dunning import grant
    from tests.test_scoped_account_statements import mutate
    foreign = {'org': seed_data['org_b_id'], 'clerk': seed_data['user_b_admin_id']}
    # Org B can retain legitimate costs from earlier tests; preserve its entire export.
    async with client(foreign) as api:
        before = rows(await api.get(f'/api/{path}/export.csv'))
    assert before[0] == (PAYMENT_HEADER if path == 'payments' else COST_HEADER)
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '1')
    await cost(migrator_session, ids, '1')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids) as api:
        assert len(rows(await api.get(f'/api/{path}/export.csv'))) == 2
        queries = {'readonly': "UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk",
            'deactivated': 'UPDATE "user" SET is_active=false WHERE id=:clerk',
            'revoked': 'UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'}
        await mutate(text(queries[change]), ids)
        result = await api.get(f'/api/{path}/export.csv')
        assert result.status_code == expected, result.text
        if change == 'revoked':
            # Revoked READ rows are absent from both list and export, not a partial EXPORT filter.
            assert rows(result) == [PAYMENT_HEADER if path == 'payments' else COST_HEADER]
    async with client(foreign) as api:
        result = await api.get(f'/api/{path}/export.csv')
        assert rows(result) == before
        own_ids = {str(value) for value in ids.values() if isinstance(value, uuid.UUID)}
        own_ids.update(str(value) for key in ('payments', 'costs') for value in ids[key])
        assert all(source_id not in result.text for source_id in own_ids)
        parent = {'bank_account_id': str(ids['bank'])} if path == 'payments' else {'property_id': str(ids['property'])}
        assert (await api.get(f'/api/{path}/export.csv', params=parent)).status_code == 404


@pytest.mark.parametrize('path', ['payments', 'cost-entries'])
async def test_live_principal_after_cached_authentication(migrator_session, scope_seed, path):
    from app.main import app
    from app.core.tenant import AuthContext, get_current_user
    from tests.test_scoped_account_statements import mutate
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await ready(migrator_session, ids)
    await mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'), ids)
    async def authenticated():
        return AuthContext(ids['clerk'], ids['org'], 'CLERK')
    app.dependency_overrides[get_current_user] = authenticated
    try:
        async with client(ids) as api:
            assert (await api.get(f'/api/{path}/export.csv')).status_code == 401
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize('path', ['payments', 'cost-entries'])
async def test_actual_scope_clock_expiry_during_render(migrator_session, scope_seed, monkeypatch, path):
    import time
    from app.api import payments, cost_entries
    from tests.test_scoped_account_statements import mutate
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '1')
    await cost(migrator_session, ids, '1')
    await ready(migrator_session, ids)
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason,expires_at) VALUES (:org,:scope,:clerk,ARRAY['read','export'],:actor,'CSV expiry',clock_timestamp()+interval '2 seconds')"), ids)
    module = payments if path == 'payments' else cost_entries
    name = '_render_payments_csv' if path == 'payments' else '_render_cost_entries_csv'
    original = getattr(module, name)
    rendered = False
    def render(items):
        nonlocal rendered
        result = original(items)
        rendered = True
        time.sleep(2.1)
        return result
    monkeypatch.setattr(module, name, render)
    async with client(ids) as api:
        response = await api.get(f'/api/{path}/export.csv')
    assert rendered and response.status_code == 404, response.text
    assert not response.content.startswith(b'\xef\xbb\xbf')


@pytest.mark.parametrize('path', ['payments', 'cost-entries'])
async def test_partial_scope_is_not_a_whole_property_report(migrator_session, scope_seed, path):
    from tests.test_scoped_dunning import grant
    from tests.test_report_source_access import property_for_clerk
    from tests.contact_source_fixtures import grant_contact
    from tests.test_property_source_enforcement import synthetic_enforcement
    ids = await fixture(migrator_session, scope_seed, whole=False)
    outside = {**ids, 'person2': uuid.uuid4(), 'tenant2': uuid.uuid4(), 'unit2': uuid.uuid4()}
    await migrator_session.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person2,:org,'Outside','Private','','','')"), outside)
    await migrator_session.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:tenant2,:org,:person2,'')"), outside)
    await migrator_session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit2,:org,:property,'outside','WOHNUNG')"), outside)
    await migrator_session.execute(text("INSERT INTO lease_agreement(organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES (:org,:unit2,:tenant2,'2025-01-01',700,100,'')"), outside)
    await payment(migrator_session, ids, '1')
    await cost(migrator_session, ids, '1')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids, 'actor') as admin:
        await grant_contact(admin, {**scope_seed, 'user_a_admin_id': ids['clerk']}, ids['person'], caps=('read', 'export'))
    await property_for_clerk(ids, scope_seed, caps=('read', 'export'))
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        listed = (await api.get(f'/api/{path}')).json()
        assert len(listed) == 1
        assert [row[0] for row in rows(await api.get(f'/api/{path}/export.csv'))[1:]] == [listed[0]['id']]


async def test_cancelled_storno_and_superseded_costs_have_list_parity(migrator_session, scope_seed):
    from datetime import datetime, timezone
    ids = await fixture(migrator_session, scope_seed, bound=False)
    original = await payment(migrator_session, ids, '200')
    reversal = await payment(migrator_session, ids, '200', direction='OUT', reverses=original)
    await migrator_session.execute(text('UPDATE payment SET reversed_by_id=:reverse WHERE id=:original'), {'original': original, 'reverse': reversal})
    cancelled = await payment(migrator_session, ids, '30', status='draft', cancelled_at=datetime.now(timezone.utc))
    old, _ = await cost(migrator_session, ids, '100')
    replacement, _ = await cost(migrator_session, ids, '150', status='needs_review')
    await migrator_session.execute(text('UPDATE cost_entry SET superseded_by_id=:new WHERE id=:old'), {'old': old, 'new': replacement})
    await ready(migrator_session, ids)
    async with client(ids) as api:
        for path, filters in [('payments', [{}, {'status': 'booked'}, {'status': 'cancelled'}]),
                              ('cost-entries', [{}, {'status': 'confirmed'}, {'status': 'needs_review'},
                                  {'status': 'confirmed', 'property_id': str(ids['property'])}])]:
            for query in filters:
                listed = (await api.get(f'/api/{path}', params=query)).json()
                data = rows(await api.get(f'/api/{path}/export.csv', params=query))
                assert [row[0] for row in data[1:]] == [row['id'] for row in listed]
        by_id = {row[0]: row for row in rows(await api.get('/api/payments/export.csv'))[1:]}
        assert by_id[str(cancelled)][8] == 'cancelled'
        costs = {row[0]: row for row in rows(await api.get('/api/cost-entries/export.csv'))[1:]}
        assert costs[str(old)][-1] == str(replacement)


async def test_every_renderer_text_field_is_defanged(monkeypatch):
    from types import SimpleNamespace
    from app.api import payments, cost_entries
    injected = '=HYPERLINK("https://example.invalid")'
    item = SimpleNamespace(id=injected, property_id=injected, bank_account_id=injected,
        value_date=injected, direction='OUT', payment_type=injected, amount=Decimal('20.00'),
        status=injected, period_year=injected, period_month=injected, tenant_profile_id=injected,
        owner_profile_id=injected, lease_agreement_id=injected, note=injected,
        invoice_date=injected, cost_category_id=injected, cost_category_plan_version=injected,
        gross_amount=Decimal('-0.01'), tenant_apportionable=injected,
        document_number=injected, superseded_by_id=injected)
    monkeypatch.setattr(payments, '_to_out', lambda row: SimpleNamespace(status=row.status))
    for render, money_columns in [(payments._render_payments_csv, {6, 7}),
                                  (cost_entries._render_cost_entries_csv, {5})]:
        content = render([item])
        data = list(csv.reader(io.StringIO(content.decode('utf-8-sig'), newline=''), delimiter=';'))[1]
        for index, value in enumerate(data):
            if index in money_columns or value == 'OUT':
                continue
            assert value == "'" + injected
        assert '-20,00' in data or '-0,01' in data


@pytest.mark.parametrize('path', ['payments', 'cost-entries'])
async def test_expired_binding_never_becomes_legacy_after_read(migrator_session, scope_seed, monkeypatch, path):
    import asyncio
    from app.repositories.payment_repo import PaymentRepository
    from app.repositories.cost_entry_repo import CostEntryRepository
    from tests.test_scoped_account_statements import mutate
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '1')
    await cost(migrator_session, ids, '1')
    await ready(migrator_session, ids)
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    await mutate(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason,expires_at) VALUES (:org,:scope,:clerk,ARRAY['read','export'],:actor,'CSV read expiry',clock_timestamp()+interval '2 seconds')"), ids)
    repository = PaymentRepository if path == 'payments' else CostEntryRepository
    original = repository.list_by_filters
    async def expire_after_read(self, **kwargs):
        selected = await original(self, **kwargs)
        assert len(selected) == 1
        await asyncio.sleep(2.1)
        return selected
    monkeypatch.setattr(repository, 'list_by_filters', expire_after_read)
    async with client(ids) as api:
        response = await api.get(f'/api/{path}/export.csv')
    assert response.status_code == 404, response.text
