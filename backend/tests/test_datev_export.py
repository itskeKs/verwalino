"""T49: explicit account configuration and independently checked EXTF postings."""
import csv
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_property_year_report import fixture, payment, cost, ready, client, snapshot
from tests.test_scoped_account_statements import mutate
from tests.test_scoped_dunning import grant


def configuration(ids, category):
    # These are deliberately synthetic ledger numbers, not suggested real assignments.
    return dict(property_id=str(ids['property']), accounting_scope_id=str(ids['scope']),
        period_start='2026-01-01', period_end='2026-03-31', fiscal_year_start='2026-01-01',
        chart='SKR03', advisor_number='12345', client_number='123', account_length=4,
        bank_accounts={str(ids['bank']): '1111'}, payment_accounts={'KALTMIETE': '2222'},
        category_accounts={str(category.id): {'account': '3333', 'counter_account': '4444'}},
        gross_accounts_reviewed=True)


@pytest.mark.asyncio
async def test_datev_q1_exact_rows_sides_dates_and_download(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '700.00', day='2026-01-01')
    await payment(migrator_session, ids, '50.25', direction='OUT', day='2026-03-31')
    await payment(migrator_session, ids, '999.00', day='2026-04-01')
    await payment(migrator_session, ids, '888.00', day='2026-02-01', status='draft')
    _, category = await cost(migrator_session, ids, '100.10', day='2026-03-31')
    await cost(migrator_session, ids, '-20.05', day='2026-01-01')
    await cost(migrator_session, ids, '999.00', day='2025-12-31')
    await cost(migrator_session, ids, '777.00', day='2026-02-01', status='needs_review')
    await ready(migrator_session, ids)
    before = await snapshot(ids)
    body = configuration(ids, category)
    await grant(ids, ['read', 'export'])
    async with client(ids) as api:
        preview = await api.post('/api/reports/datev/preview', json=body)
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert data['payment_total'] == '649.75'
        assert data['cost_total'] == '80.05'
        assert len(data['rows']) == 4
        download = await api.post('/api/reports/datev.csv', json={**body, 'expected_source_digest': data['source_digest']})
        assert download.status_code == 200, download.text
        assert 'EXTF_' in download.headers['content-disposition']
        assert download.headers['cache-control'] == 'private, no-store'
        assert download.content.startswith(b'\xef\xbb\xbf')
        assert b'\r\n' in download.content
        rows = list(csv.reader(io.StringIO(download.content.decode('utf-8-sig')), delimiter=';'))
        assert rows[0][:5] == ['EXTF', '700', '21', 'Buchungsstapel', '13']
        assert rows[0][10:16] == ['12345', '123', '20260101', '4', '20260101', '20260331']
        assert rows[0][20] == '1'  # Never silently disable DATEV's fixation.
        assert rows[0][26] == '03'
        assert len(rows[0]) == 31
        assert len(rows[1]) == 125
        assert all(len(row) == 125 for row in rows[2:])
        postings = {(r[0], r[1], r[6], r[7], r[9]) for r in rows[2:]}
        assert postings == {('700,00','S','1111','2222','0101'),
                            ('50,25','H','1111','2222','3103'),
                            ('100,10','S','3333','4444','3103'),
                            ('20,05','H','3333','4444','0101')}
        assert all(r[8] == '' for r in rows[2:])
        assert all(r[19] == '' for r in rows[2:])  # no invented DATEV receipt links
    assert await snapshot(ids) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [dict(chart=None), dict(chart='SKR42'), dict(advisor_number='1'),
    dict(client_number='0'), dict(account_length=3), dict(gross_accounts_reviewed=False),
    dict(bank_accounts={}), dict(payment_accounts={}), dict(category_accounts={}),
    dict(period_end='2025-12-31'), dict(fiscal_year_start='2027-01-01')])
async def test_datev_missing_or_invalid_configuration_fails_closed(migrator_session, scope_seed, change):
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '700', day='2026-01-01')
    _, category = await cost(migrator_session, ids, '100', day='2026-01-02')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids) as api:
        response = await api.post('/api/reports/datev/preview', json={**configuration(ids, category), **change})
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_datev_sources_skr04_and_stale_download(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '700', day='2026-01-01')
    _, category = await cost(migrator_session, ids, '100', day='2026-01-02')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    body = {**configuration(ids, category), 'chart': 'SKR04'}
    async with client(ids) as api:
        sources = await api.get('/api/reports/datev/sources', params={k:body[k] for k in
            ['property_id','accounting_scope_id','period_start','period_end']})
        assert sources.status_code == 200, sources.text
        assert sources.json()['payment_types'] == ['KALTMIETE']
        assert sources.json()['categories'][0]['id'] == str(category.id)
        assert sources.json()['banks'][0]['id'] == str(ids['bank'])
        preview = await api.post('/api/reports/datev/preview', json=body)
        assert preview.status_code == 200, preview.text
        content = await api.post('/api/reports/datev.csv', json={**body, 'expected_source_digest': preview.json()['source_digest']})
        assert content.status_code == 200, content.text
        assert list(csv.reader(io.StringIO(content.content.decode('utf-8-sig')), delimiter=';'))[0][26] == '04'
        assert (await api.post('/api/reports/datev.csv', json=body)).status_code == 422
        assert (await api.post('/api/reports/datev.csv', json={**body, 'expected_source_digest':'0'*64})).status_code == 409
        await mutate(text("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unclassified')"), ids)
        # New unclassified input is rejected rather than omitted from the old preview.
        assert (await api.post('/api/reports/datev.csv', json={**body, 'expected_source_digest':preview.json()['source_digest']})).status_code == 409


@pytest.mark.asyncio
async def test_datev_export_rights_not_read_and_live_revocation(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    _, category = await cost(migrator_session, ids, '100', day='2026-01-02')
    await ready(migrator_session, ids)
    body = configuration(ids, category)
    async with client(ids) as api:
        # scope_fixture deliberately grants read only to this clerk.
        response = await api.post('/api/reports/datev/preview', json=body)
        assert response.status_code in (403,404), response.text


def test_datev_renderer_quotes_text_and_keeps_exact_decimal():
    from app.services.datev_export import DatevRequest, DatevPosting, render_datev
    ids = {'property': uuid.uuid4(), 'scope': uuid.uuid4(), 'bank':uuid.uuid4()}
    category = type('Category', (), {'id':uuid.uuid4()})()
    config = DatevRequest(**configuration(ids, category))
    row = DatevPosting(source_type='payment', source_id=uuid.uuid4(), day=date(2026,1,2),
        amount=Decimal('0.01'), side='S', account='1111', counter_account='2222', text='Miete; "Test"')
    raw = render_datev(config, [row], generated_at=datetime(2026,4,1,tzinfo=timezone.utc))
    parsed = list(csv.reader(io.StringIO(raw.decode('utf-8-sig')), delimiter=';'))
    assert parsed[0][5] == '20260401000000000'
    assert parsed[2][0] == '0,01'
    assert parsed[2][13] == 'Miete; "Test"'
    assert '"Miete; ""Test"""' in raw.decode('utf-8-sig')


@pytest.mark.asyncio
async def test_datev_preview_rejects_unrenderable_amount(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    _, category = await cost(migrator_session, ids, '10000000000.00', day='2026-01-02')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    body = configuration(ids, category)
    body['bank_accounts'] = {}
    body['payment_accounts'] = {}
    async with client(ids) as api:
        response = await api.post('/api/reports/datev/preview', json=body)
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_datev_credit_preserves_cents_with_small_decimal_context(migrator_session, scope_seed):
    from decimal import localcontext
    ids = await fixture(migrator_session, scope_seed)
    _, category = await cost(migrator_session, ids, '-1234567.89', day='2026-01-02')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    body = {**configuration(ids, category), 'bank_accounts':{}, 'payment_accounts':{}}
    async with client(ids) as api:
        with localcontext() as context:
            context.prec = 6
            response = await api.post('/api/reports/datev/preview', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['rows'][0]['amount'] == '1234567.89'
    assert response.json()['cost_total'] == '-1234567.89'


@pytest.mark.asyncio
async def test_datev_parallel_repetition_is_read_only_and_identical(migrator_session, scope_seed):
    import asyncio
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '700', day='2026-01-01')
    _, category = await cost(migrator_session, ids, '100', day='2026-01-02')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    before = await snapshot(ids)
    async with client(ids) as api:
        responses = await asyncio.gather(*(api.post('/api/reports/datev/preview', json=configuration(ids, category)) for _ in range(2)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    assert await snapshot(ids) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('guard', ['scope', 'property_financial_disclosure_access', 'report_scope_source_access'])
async def test_datev_export_authority_rechecked_after_render(migrator_session, scope_seed, monkeypatch, guard):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.api import reports
    from app.services.authorization_service import AuthorizationService
    from app.core.rbac import ScopeCapability
    ids = await fixture(migrator_session, scope_seed)
    _, category = await cost(migrator_session, ids, '100', day='2026-01-02')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    body = {**configuration(ids, category), 'bank_accounts':{}, 'payment_accounts':{}}
    rendered = False
    denied = False
    original_render, original_scalar = reports.render_datev, AsyncSession.scalar
    original_require = AuthorizationService.require

    def render(*args, **kwargs):
        nonlocal rendered
        result = original_render(*args, **kwargs)
        rendered = True
        return result

    async def scalar(self, statement, *args, **kwargs):
        nonlocal denied
        if rendered and guard in str(statement) and "'export'" in str(statement):
            denied = True
            return False
        return await original_scalar(self, statement, *args, **kwargs)

    async def require(self, scope_id, capability):
        nonlocal denied
        if rendered and guard == 'scope' and capability == ScopeCapability.EXPORT:
            denied = True
            raise HTTPException(404, 'Export revoked')
        return await original_require(self, scope_id, capability)

    monkeypatch.setattr(reports, 'render_datev', render)
    monkeypatch.setattr(AsyncSession, 'scalar', scalar)
    monkeypatch.setattr(AuthorizationService, 'require', require)
    async with client(ids) as api:
        preview = await api.post('/api/reports/datev/preview', json=body)
        assert preview.status_code == 200, preview.text
        response = await api.post('/api/reports/datev.csv', json={**body, 'expected_source_digest':preview.json()['source_digest']})
    assert rendered and denied
    assert response.status_code == 404
    assert not response.content.startswith(b'\xef\xbb\xbf')


@pytest.mark.asyncio
async def test_datev_waits_for_financial_input_boundary(migrator_session, scope_seed):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    ids = await fixture(migrator_session, scope_seed)
    _, category = await cost(migrator_session, ids, '100', day='2026-01-02')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    body = {**configuration(ids, category), 'bank_accounts':{}, 'payment_accounts':{}}
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.connect() as connection, client(ids) as api:
            await connection.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                {'key':f"financial-inputs:{ids['org']}"})
            blocker = await connection.scalar(text('SELECT pg_backend_pid()'))
            task = asyncio.create_task(api.post('/api/reports/datev/preview', json=body))
            waiting = False
            try:
                for _ in range(200):
                    waiting = await connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks l WHERE l.locktype='advisory' AND NOT l.granted AND :blocker=ANY(pg_blocking_pids(l.pid)))"), {'blocker':blocker})
                    if waiting:
                        break
                    await asyncio.sleep(.01)
            finally:
                await connection.rollback()
            response = await asyncio.wait_for(task, timeout=15)
            assert response.status_code == 200, response.text
            assert waiting, 'DATEV must use the existing shared financial-input boundary'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['property', 'contact'])
async def test_datev_requires_complete_source_export(migrator_session, scope_seed, source):
    from tests.contact_source_fixtures import grant_contact
    from tests.test_report_source_access import property_for_clerk
    from tests.test_property_source_enforcement import synthetic_enforcement
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '700', day='2026-01-01')
    _, category = await cost(migrator_session, ids, '100', day='2026-01-02')
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids, 'actor') as admin:
        contact_domain, contact_receipt = await grant_contact(admin,
            {**scope_seed, 'user_a_admin_id': ids['clerk']}, ids['person'],
            caps=('read',) if source == 'contact' else ('read','export'))
    domain, receipt = await property_for_clerk(ids, scope_seed,
        caps=('read',) if source == 'property' else ('read','export'))
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        body = configuration(ids, category)
        assert (await api.post('/api/reports/datev/preview', json=body)).status_code == 404
        async with client(ids, 'actor') as admin:
            prefix, chosen_domain, chosen_receipt = ('property',domain,receipt) if source == 'property' else ('person',contact_domain,contact_receipt)
            base = f'/api/{prefix}-data-domains/{chosen_domain}/grants'
            assert (await admin.post(f'{base}/{chosen_receipt}/revoke')).status_code == 204
            grant_body = {'user_id':str(ids['clerk']), 'capabilities':['read','export']}
            grant_body.update({'reason':'DATEV export'} if source == 'property' else {'reason_code':'TEAM_ACCESS'})
            allowed = await admin.post(base, headers={'Idempotency-Key':str(uuid.uuid4())}, json=grant_body)
            assert allowed.status_code == 201, allowed.text
        preview = await api.post('/api/reports/datev/preview', json=body)
        assert preview.status_code == 200, preview.text
        async with client(ids, 'actor') as admin:
            assert (await admin.post(f"{base}/{allowed.json()['id']}/revoke")).status_code == 204
        response = await api.post('/api/reports/datev.csv', json={**body,'expected_source_digest':preview.json()['source_digest']})
        assert response.status_code == 404, response.text
        assert not response.content.startswith(b'\xef\xbb\xbf')


@pytest.mark.asyncio
async def test_datev_current_projection_all_types_storno_corrections_and_zero(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    types = ['KALTMIETE','NK_VORAUSZAHLUNG','HAUSGELD_VORSCHUSS','RUECKLAGENANTEIL','SONDERZAHLUNG','NACHZAHLUNG','KAUTION']
    live_payments = []
    for kind in types:
        live_payments.append(await payment(migrator_session, ids, '1.01', kind=kind, day='2026-01-01'))
    original = await payment(migrator_session, ids, '999', day='2026-01-02')
    reversal = await payment(migrator_session, ids, '999', direction='OUT', reverses=original, day='2026-01-02')
    await migrator_session.execute(text('UPDATE payment SET reversed_by_id=:reversal WHERE id=:original'), {'reversal':reversal,'original':original})
    await payment(migrator_session, ids, '999', status='draft', cancelled_at=datetime.now(timezone.utc), day='2026-01-02')
    old, category = await cost(migrator_session, ids, '999', day='2026-01-02')
    current, _ = await cost(migrator_session, ids, '-20', day='2026-01-02')
    await migrator_session.execute(text('UPDATE cost_entry SET superseded_by_id=:current WHERE id=:old'), {'current':current,'old':old})
    await cost(migrator_session, ids, '0', day='2026-01-02')
    await cost(migrator_session, ids, '999', status='rejected', day='2026-01-02')
    await ready(migrator_session, ids)
    await grant(ids, ['read','export'])
    before = await snapshot(ids)
    body = {**configuration(ids, category), 'accounting_scope_id':None,
            'payment_accounts':{kind:str(2200+i) for i,kind in enumerate(types)}}
    async with client(ids) as api:
        response = await api.post('/api/reports/datev/preview', json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data['payment_total'],data['cost_total']) == ('7.07','-20.00')
    assert len(data['rows']) == 8
    assert {r['source_id'] for r in data['rows']} == {str(i) for i in [*live_payments,current]}
    assert {r['counter_account'] for r in data['rows'] if r['source_type']=='payment'} == {str(2200+i) for i in range(7)}
    assert next(r for r in data['rows'] if r['source_type']=='cost')['side'] == 'H'
    assert await snapshot(ids) == before
