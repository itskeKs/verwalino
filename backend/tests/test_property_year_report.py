"""Object-year report: actual booked money, invoice costs and complete read sources."""
import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.conftest import MIGRATOR_URL

from app.core.config import settings
from app.core.security import create_session_token
from app.main import app
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scope_grants import scope_fixture, bind
from tests.test_scoped_account_statements import mutate
from tests.test_scoped_dunning import grant

pytestmark = pytest.mark.asyncio
ANLAGE_V = '/api/reports/property-year/anlage-v'


async def fixture(session, seed, *, bound=True, management_type='MISCH', whole=True,
                  scope_type='MIET', start='2025-01-01', end='2026-12-31'):
    ids = await scope_fixture(session, seed)
    ids.update(person=uuid.uuid4(), tenant=uuid.uuid4(), owner=uuid.uuid4())
    await session.execute(text("UPDATE property SET name='Jahresobjekt',management_type=:kind WHERE id=:property"), {**ids, 'kind': management_type})
    # Scope rows are immutable; create the intended period rather than editing one.
    await session.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant"), ids)
    ids['scope'] = uuid.uuid4()
    await session.execute(text("""INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,valid_to,whole_property,origin,created_by)
        VALUES (:scope,:org,:property,:kind,'Year report',CAST(:start AS date),CAST(:end AS date),:whole,'REVIEWED',:actor)"""),
        {**ids, 'kind': scope_type, 'start': date.fromisoformat(start), 'end': date.fromisoformat(end), 'whole': whole})
    await session.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Year','Reader','','','')"), ids)
    for table, key in [('tenant_profile','tenant'), ('owner_profile','owner')]:
        await session.execute(text(f"INSERT INTO {table}(id,organization_id,person_id,notes) VALUES (:{key},:org,:person,'')"), ids)
    if not whole:
        ids['unit'] = uuid.uuid4()
        await session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit,:org,:property,'1','WOHNUNG')"), ids)
        if scope_type == 'SEV':
            await session.execute(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from) VALUES (:org,:unit,:owner,'2024-01-01')"), ids)
        await session.execute(text('INSERT INTO accounting_scope_unit(organization_id,scope_id,unit_id,property_id,owner_profile_id) VALUES (:org,:scope,:unit,:property,:scope_owner)'), {**ids, 'scope_owner':ids['owner'] if scope_type == 'SEV' else None})
    await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:clerk,ARRAY['read'],:actor,'Read only report')"), ids)
    if scope_type == 'SEV':
        await session.execute(text('DELETE FROM bank_account WHERE id=:bank'), ids)
    ids.update(bound=bound, skip_bank=scope_type == 'SEV', payments=[], costs=[])
    return ids


async def payment(session, ids, amount, *, kind='KALTMIETE', direction='IN', day='2026-06-01', status='booked', reverses=None, **extra):
    key = uuid.uuid4()
    await session.execute(text("""INSERT INTO payment(id,organization_id,property_id,bank_account_id,amount,direction,value_date,payment_type,tenant_profile_id,owner_profile_id,period_year,period_month,status,reverses_id)
        VALUES (:id,:org,:property,:bank,:amount,:direction,CAST(:day AS date),:kind,:tenant_id,:owner_id,2025,1,:status,:reverses)"""),
        {**ids, 'id': key, 'amount': Decimal(amount), 'direction': direction, 'day': date.fromisoformat(day), 'kind': kind, 'status': status, 'reverses': reverses,
         'tenant_id': ids['tenant'] if kind in ('KALTMIETE','NK_VORAUSZAHLUNG','KAUTION','NACHZAHLUNG') else None,
         'owner_id': ids['owner'] if kind in ('HAUSGELD_VORSCHUSS','RUECKLAGENANTEIL','SONDERZAHLUNG') else None})
    if extra:
        for column, value in extra.items():
            assert column in {'reversed_by_id','reverses_id','cancelled_at'}
            await session.execute(text(f'UPDATE payment SET {column}=:value WHERE id=:id'), {'id': key, 'value': value})
    ids['payments'].append(key)
    return key


async def cost(session, ids, amount, *, code='GRUNDSTEUER', apportion='UMLAGEFAEHIG', day='2026-06-01', status='confirmed'):
    key = uuid.uuid4()
    category = (await session.execute(text('SELECT id,code,label FROM cost_category WHERE code=:code AND plan_version=1'), {'code': code})).one()
    await session.execute(text("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,cost_category_plan_version,invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source,confirmed_at,confirmed_by)
        VALUES (:id,:org,:property,:category,1,CAST(:day AS date),'2025-01-01','2025-12-31',:amount,:apportion,:status,'MANUAL',CASE WHEN CAST(:status AS varchar)='confirmed' THEN now() END,CASE WHEN CAST(:status AS varchar)='confirmed' THEN CAST(:actor AS uuid) END)"""),
        {**ids, 'id': key, 'category': category.id, 'amount': Decimal(amount), 'apportion': apportion, 'day': date.fromisoformat(day), 'status': status})
    ids['costs'].append(key)
    return key, category


async def ready(session, ids):
    if ids['bound'] and not ids['skip_bank']:
        await bind(session, ids)
        for key in ids['payments']:
            await session.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:id,:bank,'REVIEWED','Report fixture',:actor)"), {**ids, 'id': key})
        for key in ids['costs']:
            await session.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,cost_entry_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:id,'REVIEWED','Report fixture',:actor)"), {**ids, 'id': key})
    await session.commit()


def client(ids, actor='clerk'):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
        cookies={settings.session_cookie_name: create_session_token(ids[actor], ids['org'])})


def query(ids, **overrides):
    return {'property_id': str(ids['property']), 'year': 2026,
            **({'accounting_scope_id': str(ids['scope'])} if ids['bound'] else {}), **overrides}


async def report(api, ids, **overrides):
    return await api.get('/api/reports/property-year', params=query(ids, **overrides))


async def test_exact_public_contract_and_read_only(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    for amount, direction in [('700','IN'), ('150','IN'), ('50','OUT')]:
        await payment(migrator_session, ids, amount, direction=direction)
    _, category = await cost(migrator_session, ids, '100')
    await cost(migrator_session, ids, '25', apportion='NICHT_UMLAGEFAEHIG')
    await migrator_session.execute(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"), ids)
    await ready(migrator_session, ids)
    before = await snapshot(ids)
    async with client(ids) as api:
        response = await report(api, ids)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {
            'property_id': str(ids['property']), 'property_name': 'Jahresobjekt', 'year': 2026,
            'period_start': '2026-01-01', 'period_end': '2026-12-31', 'accounting_scope_id': str(ids['scope']),
            'income_date_basis': 'value_date', 'expense_date_basis': 'invoice_date',
            'footnote': body['footnote'],
            'income': [{'payment_type': 'KALTMIETE', 'amount': '800.00'}],
            'expenses': [{'category_id': str(category.id), 'category_code': category.code, 'category_name': category.label,
                          'tenant_apportionability': flag, 'amount': amount} for flag, amount in
                         [('NICHT_UMLAGEFAEHIG','25.00'), ('UMLAGEFAEHIG','100.00')]],
            'income_total': '800.00', 'expense_total': '125.00', 'balance': '675.00'}
        assert 'Steuererklärung' in body['footnote']
        assert (await report(api, ids)).json() == body
    assert await snapshot(ids) == before


async def snapshot(ids):
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.connect() as session:
            result = {}
            for table in ['payment','cost_entry','audit_event','settlement_run','economic_plan']:
                result[table] = (await session.execute(text(f'SELECT to_jsonb(t) FROM {table} t WHERE organization_id=:org ORDER BY id'), ids)).scalars().all()
            return result
    finally:
        await engine.dispose()


async def test_value_and_invoice_dates_not_service_or_payment_period(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    for day in ['2025-12-31','2027-01-01']:
        await payment(migrator_session, ids, '999', day=day)
        await cost(migrator_session, ids, '999', day=day)
    await payment(migrator_session, ids, '0.01', day='2026-01-01')
    await payment(migrator_session, ids, '1.02', day='2026-12-31')
    await cost(migrator_session, ids, '0.03', day='2026-12-31')
    await payment(migrator_session, ids, '999', status='draft')
    await cost(migrator_session, ids, '999', status='needs_review')
    await migrator_session.execute(text("INSERT INTO bank_transaction(organization_id,bank_account_id,booking_date,value_date,amount,import_hash,status) VALUES (:org,:bank,'2026-06-01','2026-06-01',999,:hash,'NEU')"), {**ids,'hash':'f'*64})
    await ready(migrator_session, ids)
    async with client(ids) as api:
        response = await report(api, ids)
        assert response.status_code == 200, response.text
        assert (response.json()['income_total'], response.json()['expense_total'], response.json()['balance']) == ('1.03','0.03','1.00')
        assert response.json()['accounting_scope_id'] is None


@pytest.mark.parametrize('year', [1,9999,2028])
async def test_authorized_empty_years_no_today_or_overflow_limit(migrator_session, scope_seed, year):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        response = await report(api, ids, year=year)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['period_start'] == date(year,1,1).isoformat()
        assert body['period_end'] == date(year,12,31).isoformat()
        assert body['income'] == body['expenses'] == []
        assert body['income_total'] == body['expense_total'] == body['balance'] == '0.00'


@pytest.mark.parametrize('override', [{'year':0},{'year':10000},{'year':'bad'},{'property_id':'bad'},{'accounting_scope_id':'bad'}])
async def test_invalid_query(migrator_session, scope_seed, override):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        assert (await report(api, ids, **override)).status_code == 422


@pytest.mark.parametrize('options', [dict(start='2026-02-01'),dict(end='2026-11-30'),dict(whole=False),dict(management_type='WEG',scope_type='MIET'),dict(management_type='MIET',scope_type='WEG'),dict(scope_type='SEV',whole=False)])
async def test_incompatible_or_partial_scope_rejected(migrator_session, scope_seed, options):
    ids = await fixture(migrator_session, scope_seed, **options)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        response = await report(api, ids)
        assert response.status_code == 422, response.text


async def test_scope_property_membership_and_unknown_ids(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        assert (await report(api, ids, property_id=str(ids['other_property']))).status_code == 422
        for override in [{'property_id':str(uuid.uuid4())}, {'accounting_scope_id':str(uuid.uuid4())}]:
            assert (await report(api, ids, **override)).status_code == 404
        omitted = query(ids)
        del omitted['accounting_scope_id']
        assert (await api.get('/api/reports/property-year', params=omitted)).status_code == 409
    async with client(ids, 'actor') as api:
        assert (await report(api, ids)).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as api:
        assert (await report(api, ids)).status_code == 401


@pytest.mark.parametrize('change', ['scope','member','user','unclassified'])
async def test_live_read_and_input_completeness(migrator_session, scope_seed, change):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        assert (await report(api, ids)).status_code == 200
        statements = {
            'scope': "UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL",
            'member': "UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk",
            'user': 'UPDATE "user" SET is_active=false WHERE id=:clerk',
            'unclassified': "INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unclassified')"}
        await mutate(text(statements[change]), ids)
        assert (await report(api, ids)).status_code == {'scope':404,'member':200,'user':401,'unclassified':409}[change]


async def test_report_preserves_cents_under_low_outer_decimal_precision(migrator_session, scope_seed):
    from decimal import localcontext

    ids = await fixture(migrator_session, scope_seed, bound=False)
    await payment(migrator_session, ids, '1234567.89')
    await payment(migrator_session, ids, '0.02', kind='NK_VORAUSZAHLUNG')
    await cost(migrator_session, ids, '0.01')
    await cost(migrator_session, ids, '0.01', apportion='NICHT_UMLAGEFAEHIG')
    await ready(migrator_session, ids)
    async with client(ids) as api:
        with localcontext() as context:
            context.prec = 6
            response = await report(api, ids)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['income_total'], body['expense_total'], body['balance']) == (
        '1234567.91', '0.02', '1234567.89')


async def test_all_types_large_aggregates_and_original_apportionability(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    types = ['KALTMIETE','NK_VORAUSZAHLUNG','HAUSGELD_VORSCHUSS','RUECKLAGENANTEIL','SONDERZAHLUNG','NACHZAHLUNG','KAUTION']
    for kind in types:
        await payment(migrator_session, ids, '0.01', kind=kind)
    for _ in range(2):
        await payment(migrator_session, ids, '999999999999.99')
        await cost(migrator_session, ids, '999999999999.99')
    for flag in ['NICHT_UMLAGEFAEHIG','TEILWEISE_PRUEFEN']:
        await cost(migrator_session, ids, '0.01', apportion=flag)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        response = await report(api, ids)
        assert response.status_code == 200, response.text
        body = response.json()
        assert {row['payment_type'] for row in body['income']} == set(types)
        assert body['income_total'] == '2000000000000.05'
        assert body['expense_total'] == '2000000000000.00'
        assert body['balance'] == '0.05'
        assert {row['tenant_apportionability']:row['amount'] for row in body['expenses']} == {
            'UMLAGEFAEHIG':'1999999999999.98','NICHT_UMLAGEFAEHIG':'0.01','TEILWEISE_PRUEFEN':'0.01'}


async def test_storno_cancelled_and_cost_correction_chains(migrator_session, scope_seed):
    from datetime import datetime, timezone
    ids = await fixture(migrator_session, scope_seed)
    original = await payment(migrator_session, ids, '999')
    reversal = await payment(migrator_session, ids, '999', direction='OUT', reverses=original)
    await migrator_session.execute(text('UPDATE payment SET reversed_by_id=:reversal WHERE id=:original'),
        {'original':original, 'reversal':reversal})
    await payment(migrator_session, ids, '700')
    await payment(migrator_session, ids, '50', direction='OUT')
    await payment(migrator_session, ids, '999', status='draft', cancelled_at=datetime.now(timezone.utc))
    old, _ = await cost(migrator_session, ids, '999')
    middle, _ = await cost(migrator_session, ids, '888')
    current, _ = await cost(migrator_session, ids, '-20')
    for previous, successor in [(old,middle),(middle,current)]:
        await migrator_session.execute(text('UPDATE cost_entry SET superseded_by_id=:next WHERE id=:old'), {'next':successor,'old':previous})
    await cost(migrator_session, ids, '25', code='KALTWASSER', apportion='TEILWEISE_PRUEFEN')
    await cost(migrator_session, ids, '999', status='rejected')
    await ready(migrator_session, ids)
    before = await snapshot(ids)
    async with client(ids) as api:
        response = await report(api, ids)
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body['income_total'],body['expense_total'],body['balance']) == ('650.00','5.00','645.00')
        assert [(row['category_code'],row['amount']) for row in body['expenses']] == [('GRUNDSTEUER','-20.00'),('KALTWASSER','25.00')]
    assert await snapshot(ids) == before


@pytest.mark.parametrize('source', ['property','contact'])
async def test_complete_sources_read_only_and_revoke_not_partial_zero(migrator_session, scope_seed, source):
    from tests.test_report_source_access import property_for_clerk
    from tests.contact_source_fixtures import grant_contact
    from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
    ids = await fixture(migrator_session, scope_seed)
    await payment(migrator_session, ids, '700')
    await cost(migrator_session, ids, '100')
    await ready(migrator_session, ids)
    async with client(ids, 'actor') as admin:
        contact_domain, contact_receipt = await grant_contact(admin,
            {**scope_seed,'user_a_admin_id':ids['clerk']}, ids['person'], caps=('read',))
    domain, receipt = await property_for_clerk(ids, scope_seed, caps=('read',))
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        response = await report(api, ids)
        assert response.status_code == 200, response.text
        assert response.json()['balance'] == '600.00'
        await change_mode(scope_seed, 'PAUSED')
        assert (await report(api, ids)).status_code == 200
        async with client(ids, 'actor') as admin:
            route = f'/api/property-data-domains/{domain}/grants/{receipt}/revoke' if source == 'property' else f'/api/person-data-domains/{contact_domain}/grants/{contact_receipt}/revoke'
            assert (await admin.post(route)).status_code == 204
        assert (await report(api, ids)).status_code == 404


async def test_other_property_and_foreign_organization_never_contribute(migrator_session, scope_seed, seed_data):
    ids = await fixture(migrator_session, scope_seed, bound=False)
    await payment(migrator_session, ids, '10')
    await cost(migrator_session, ids, '2')
    other = {**ids,'property':ids['other_property'],'bank':uuid.uuid4(), 'payments':[], 'costs':[]}
    await migrator_session.execute(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:bank,:org,:property,'LAUFEND','Other property')"), other)
    await payment(migrator_session, other, '900')
    await cost(migrator_session, other, '900')
    await ready(migrator_session, ids)
    async with client(ids) as api:
        response = await report(api, ids)
        assert response.status_code == 200, response.text
        assert response.json()['balance'] == '8.00'
    foreign = {'org':seed_data['org_b_id'], 'clerk':seed_data['user_b_admin_id']}
    async with client(foreign) as api:
        assert (await report(api, ids)).status_code == 404
        assert (await report(api, ids,accounting_scope_id=str(ids['scope']))).status_code == 404


async def test_live_member_guard_even_after_authentication(migrator_session, scope_seed):
    from app.core.tenant import AuthContext, get_current_user
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await mutate(text('UPDATE "user" SET is_active=false WHERE id=:clerk'), ids)
    async def already_authenticated():
        return AuthContext(ids['clerk'],ids['org'],'CLERK')
    app.dependency_overrides[get_current_user] = already_authenticated
    try:
        async with client(ids) as api:
            assert (await report(api, ids)).status_code == 401
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize('kind,payment_type', [('MIET','KALTMIETE'),('WEG','HAUSGELD_VORSCHUSS')])
async def test_compatible_property_scope_types(migrator_session, scope_seed, kind, payment_type):
    ids = await fixture(migrator_session, scope_seed, management_type=kind, scope_type=kind)
    await payment(migrator_session, ids, '10.01', kind=payment_type)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        response = await report(api, ids)
        assert response.status_code == 200, response.text
        assert response.json()['income'] == [{'payment_type':payment_type,'amount':'10.01'}]


async def test_existing_foreign_scope_is_not_disclosed(migrator_session, scope_seed, seed_data):
    ids = await fixture(migrator_session, scope_seed)
    foreign = {'org':seed_data['org_b_id'], 'actor':seed_data['user_b_admin_id'],
               'property':uuid.uuid4(), 'scope':uuid.uuid4()}
    await migrator_session.execute(text("INSERT INTO property(id,organization_id,name,street,zip_code,city,management_type) VALUES (:property,:org,'Foreign','','','','MIET')"), foreign)
    await migrator_session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:scope,:org,:property,'MIET','Foreign scope','2025-01-01',true,'REVIEWED',:actor)"), foreign)
    await migrator_session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:actor,ARRAY['read'],:actor,'Foreign reader')"), foreign)
    await ready(migrator_session, ids)
    async with client(ids) as api:
        assert (await report(api, ids, accounting_scope_id=str(foreign['scope']))).status_code == 404
        assert (await report(api, ids, property_id=str(foreign['property']))).status_code == 404


async def test_anlage_v_summary_keeps_unmapped_exclusions_and_reconciles_without_writes(migrator_session, scope_seed):
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
        response = await api.get(ANLAGE_V, params=query(ids))
        assert response.status_code == 200, response.text
        summary = response.json()
        assert (summary['property_id'], summary['year'], summary['accounting_scope_id']) == (
            str(ids['property']), 2026, str(ids['scope']))
        assert {row['code'] for row in summary['unmapped']} == {'KAUTION', 'ERHALTUNGSRUECKLAGE'}
        assert [(row['mapped_total'], row['unmapped_total'], row['report_total'], row['difference'])
                for row in summary['reconciliation']] == [
                    ('1384.56', '1800.00', '3184.56', '0.00'), ('100.03', '20.00', '120.03', '0.00')]
    assert await snapshot(ids) == before


async def test_anlage_v_summary_foreign_unknown_and_unauthenticated_requests_keep_report_guards(migrator_session, scope_seed, seed_data):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    foreign = {'org': seed_data['org_b_id'], 'clerk': seed_data['user_b_admin_id']}
    async with client(foreign) as api:
        assert (await api.get(ANLAGE_V, params=query(ids))).status_code == 404
        assert (await api.get(ANLAGE_V, params=query(ids, property_id=str(uuid.uuid4()), accounting_scope_id=str(uuid.uuid4())))).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
        assert (await api.get(ANLAGE_V, params=query(ids))).status_code == 401
    async with client(ids) as api:
        assert (await api.get(ANLAGE_V, params=query(ids, year=0))).status_code == 422
        assert (await api.get(ANLAGE_V, params=query(ids, accounting_scope_id=str(uuid.uuid4())))).status_code == 404
        assert (await api.get(ANLAGE_V, params={'property_id': str(ids['property']), 'year': 2026})).status_code == 409


async def test_read_access_is_enough_for_anlage_v_summary(migrator_session, scope_seed):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"), ids)
    async with client(ids) as api:
        assert (await api.get(ANLAGE_V, params=query(ids))).status_code == 200


@pytest.mark.parametrize('change,expected', [('scope', 404), ('user', 401), ('unclassified', 409)])
async def test_anlage_v_summary_reuses_live_authority_and_complete_sources(migrator_session, scope_seed, change, expected):
    ids = await fixture(migrator_session, scope_seed)
    await ready(migrator_session, ids)
    await grant(ids, ['read', 'export'])
    async with client(ids) as api:
        assert (await api.get(ANLAGE_V, params=query(ids))).status_code == 200
        statements = {
            'scope': "UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL",
            'user': 'UPDATE "user" SET is_active=false WHERE id=:clerk',
            'unclassified': "INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unclassified')",
        }
        await mutate(text(statements[change]), ids)
        assert (await api.get(ANLAGE_V, params=query(ids))).status_code == expected
