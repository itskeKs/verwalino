"""T21: real bank matching and atomic, tenant-safe payment booking."""
import asyncio
import uuid
from decimal import Decimal
from datetime import date

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tests.conftest import MIGRATOR_URL

from app.core.config import settings
from app.core.security import create_session_token
from app.main import app
from app.services.payment_service import PaymentService
from tests.financial_coverage_helpers import attest_fixture
from tests.test_invoice_extraction_worker import worker_tenant  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed  # noqa: F401
from tests.test_scoped_account_statements import statement_fixture, client as scope_client

pytestmark = pytest.mark.asyncio
BASE = '/api/bank-transactions'


@pytest_asyncio.fixture
async def bank_db(seed_data):
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def bank_seed(worker_tenant, bank_db):
    api, seed = worker_tenant
    ids = {key: uuid.uuid4() for key in ('property', 'other_property', 'bank', 'other_bank',
        'unit', 'other_unit', 'person', 'tenant', 'lease', 'other_lease', 'owner', 'owner_person', 'reader')}
    ids.update(org=seed['org_a_id'], actor=seed['user_a_admin_id'])
    s = bank_db
    for prop in ('property', 'other_property'):
        await s.execute(text("INSERT INTO property(id,organization_id,name,management_type) VALUES (:id,:org,'Bank matching','WEG')"), {**ids, 'id': ids[prop]})
    for bank, prop in [('bank','property'), ('other_bank','other_property')]:
        await s.execute(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:id,:org,:prop,'LAUFEND','Matching')"), {**ids, 'id': ids[bank], 'prop': ids[prop]})
    for unit, prop in [('unit','property'), ('other_unit','other_property')]:
        await s.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type,area,heated_area,mea) VALUES (:id,:org,:prop,'1','WOHNUNG',100,100,1000)"), {**ids, 'id': ids[unit], 'prop': ids[prop]})
    for person in ('person', 'owner_person'):
        await s.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,iban) VALUES (:id,:org,'Bank','Person','DE89370400440532013000')"), {**ids, 'id': ids[person]})
    await s.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id) VALUES (:tenant,:org,:person)"), ids)
    await s.execute(text("INSERT INTO owner_profile(id,organization_id,person_id) VALUES (:owner,:org,:owner_person)"), ids)
    for lease, unit in [('lease','unit'), ('other_lease','other_unit')]:
        await s.execute(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment) VALUES (:id,:org,:unit_id,:tenant,'2025-01-01',700,150)"), {**ids, 'id': ids[lease], 'unit_id': ids[unit]})
    await s.execute(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from,mea_share) VALUES (:org,:unit,:owner,'2020-01-01',1000)"), ids)
    await s.execute(text('INSERT INTO "user"(id,organization_id,email,password_hash,display_name) VALUES (:reader,:org,:email,\'fixture\',\'Reader\')'), {**ids, 'email': f"{ids['reader']}@bank.invalid"})
    await s.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:reader,'READ_ONLY')"), ids)
    await s.commit()
    return api, ids


async def transaction(s, ids, *, amount='850.00', purpose='Warmmiete Juni 2025', bank='bank', value_date='2025-06-03'):
    tid = uuid.uuid4()
    await s.execute(text("""INSERT INTO bank_transaction(id,organization_id,bank_account_id,booking_date,value_date,amount,counterparty_name,counterparty_iban,purpose,import_hash,status)
      VALUES (:id,:org,:bank,'2025-06-03',CAST(:value AS date),:amount,'Bank Person','DE89370400440532013000',:purpose,:hash,'NEU')"""),
      {'id': tid, 'org': ids['org'], 'bank': ids[bank], 'value': date.fromisoformat(value_date),
       'amount': Decimal(amount), 'purpose': purpose, 'hash': tid.hex})
    await s.commit()
    return str(tid)


def split(ids):
    return {'lines': [{'payment_type': kind, 'amount': amount, 'period_year': 2025, 'period_month': 6,
        'lease_agreement_id': str(ids['lease']), 'tenant_profile_id': str(ids['tenant'])}
        for kind, amount in [('KALTMIETE','700.00'), ('NK_VORAUSZAHLUNG','150.00')]]}


async def listing(api, ids, **params):
    return await api.get(BASE, params={'bank_account_id': str(ids['bank']), **params})


async def test_warm_rent_suggestions_booking_audit_retention_and_statement(bank_seed, bank_db):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids)
    before = await listing(api, ids)
    assert before.status_code == 200, before.text
    suggestion = before.json()['items'][0]['suggestions'][0]
    assert suggestion['confidence'] == 'HIGH'
    assert [Decimal(line['amount']) for line in suggestion['lines']] == [Decimal('700'), Decimal('150')]
    assert all(line['lease_agreement_id'] == str(ids['lease']) for line in suggestion['lines'])
    result = await api.post(f'{BASE}/{tid}/assign', json=split(ids))
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'ZUGEORDNET'
    payments = result.json()['payments']
    assert len(payments) == 2
    for p in payments:
        assert p['status'] == 'booked' and p['bank_transaction_id'] == tid
        assert p['bank_account_id'] == str(ids['bank']) and p['property_id'] == str(ids['property'])
        assert p['direction'] == 'IN' and p['value_date'] == '2025-06-03'
        assert p['booked_at'] and p['booked_by'] == str(ids['actor'])
        assert p['retention_until'] == '2033-12-31'
    audit = (await bank_db.execute(text("SELECT action FROM audit_event WHERE entity_id IN (SELECT id FROM payment WHERE bank_transaction_id=:id)"), {'id': uuid.UUID(tid)})).scalars().all()
    assert sorted(audit) == ['payment.booked', 'payment.booked', 'payment.created', 'payment.created']
    statement = await api.get(f"/api/payments/mietkonto/lease/{ids['lease']}", params={'period_start':'2025-06-01','period_end':'2025-06-30'})
    assert statement.status_code == 200, statement.text
    assert Decimal(statement.json()['balance']) == 0
    assert (await listing(api, ids)).json()['items'] == []
    after = (await listing(api, ids, status='ZUGEORDNET')).json()['items'][0]
    assert after['suggestions'] == []
    assert (await api.post(f'{BASE}/{tid}/assign', json=split(ids))).status_code == 409
    assert (await api.post(f'{BASE}/{tid}/ignore')).status_code == 409
    with pytest.raises(DBAPIError):
        async with bank_db.begin_nested():
            await bank_db.execute(text('UPDATE payment SET bank_transaction_id=NULL WHERE bank_transaction_id=:id'), {'id': uuid.UUID(tid)})


async def test_explicit_month_not_value_month_and_partial_splits(bank_seed, bank_db):
    api, ids = bank_seed
    await transaction(bank_db, ids, purpose='Warmmiete März 2025')
    s = (await listing(api, ids)).json()['items'][0]['suggestions'][0]
    assert {line['period_month'] for line in s['lines']} == {3}
    await transaction(bank_db, ids, amount='600.00')
    items = (await listing(api, ids)).json()['items']
    partial = next(row for row in items if row['amount'] == '600.00')['suggestions'][0]
    assert partial['requires_review'] and all(line['amount'] is None for line in partial['lines'])
    assert partial['unallocated_amount'] == '600.00'


@pytest.mark.parametrize('change', [
    {'amount':'849.99'}, {'amount':None}, {'amount':'-1'}, {'amount':'0'}, {'amount':'NaN'},
    {'amount':'Infinity'}, {'amount':'700.001'}, {'amount':'1000000000000.00'},
    {'period_month':0}, {'period_month':13}, {'period_year':0}, {'period_year':32768},
    {'payment_type':'UNKNOWN'}, {'direction':'OUT'}, {'property_id':str(uuid.uuid4())},
    {'bank_account_id':str(uuid.uuid4())}, {'lease_agreement_id':None},
    {'owner_profile_id':str(uuid.uuid4())},
])
async def test_invalid_lines_fail_closed(bank_seed, bank_db, change):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids)
    body = split(ids)
    body['lines'][0].update(change)
    response = await api.post(f'{BASE}/{tid}/assign', json=body)
    assert response.status_code == 422, response.text
    assert await bank_db.scalar(text('SELECT count(*) FROM payment WHERE bank_transaction_id=:id'), {'id':uuid.UUID(tid)}) == 0


@pytest.mark.parametrize('size', [0, 101])
async def test_line_count_limit(bank_seed, bank_db, size):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids)
    assert (await api.post(f'{BASE}/{tid}/assign', json={'lines': [split(ids)['lines'][0]] * size})).status_code == 422


async def test_target_coherence_and_foreign_ids(bank_seed, bank_db, client_b_admin):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids)
    for field in ('lease_agreement_id', 'tenant_profile_id'):
        body = split(ids)
        body['lines'][0][field] = str(uuid.uuid4())
        assert (await api.post(f'{BASE}/{tid}/assign', json=body)).status_code == 404
    body = split(ids)
    body['lines'][0]['lease_agreement_id'] = str(ids['other_lease'])
    assert (await api.post(f'{BASE}/{tid}/assign', json=body)).status_code == 422
    assert (await client_b_admin.post(f'{BASE}/{tid}/assign', json=split(ids))).status_code == 404
    assert (await client_b_admin.post(f'{BASE}/{tid}/ignore')).status_code == 404
    assert (await listing(client_b_admin, ids)).status_code == 404
    assert (await client_b_admin.get(BASE)).json()['items'] == []


async def test_reader_unauth_and_ignore(bank_seed, bank_db):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids)
    for user, expected in [(ids['reader'],403), (None,401)]:
        cookies = {settings.session_cookie_name:create_session_token(user,ids['org'])} if user else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',cookies=cookies) as client:
            assert (await client.get(BASE)).status_code == (200 if user else 401)
            assert (await client.post(f'{BASE}/{tid}/assign',json=split(ids))).status_code == expected
            assert (await client.post(f'{BASE}/{tid}/ignore')).status_code == expected
    response = await api.post(f'{BASE}/{tid}/ignore')
    assert response.status_code == 200 and response.json()['status'] == 'IGNORIERT'
    assert (await api.post(f'{BASE}/{tid}/ignore')).status_code == 409
    assert (await api.post(f'{BASE}/{tid}/assign',json=split(ids))).status_code == 409
    assert await bank_db.scalar(text('SELECT count(*) FROM payment WHERE bank_transaction_id=:id'), {'id':uuid.UUID(tid)}) == 0


async def test_negative_transaction_uses_positive_out_payments(bank_seed, bank_db):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids,amount='-850.00')
    response = await api.post(f'{BASE}/{tid}/assign',json=split(ids))
    assert response.status_code == 200, response.text
    assert all(p['direction']=='OUT' and Decimal(p['amount'])>0 for p in response.json()['payments'])


@pytest.mark.parametrize('ignore', [False, True])
async def test_concurrent_pending_transition(bank_seed, bank_db, ignore):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids)
    async def assign():
        return await api.post(f'{BASE}/{tid}/assign',json=split(ids))
    first, second = await asyncio.gather(assign(), api.post(f'{BASE}/{tid}/ignore') if ignore else assign())
    assert sorted([first.status_code, second.status_code]) == [200,409], (first.text,second.text)
    count = await bank_db.scalar(text('SELECT count(*) FROM payment WHERE bank_transaction_id=:id'),{'id':uuid.UUID(tid)})
    assert count == (0 if ignore and second.status_code==200 else 2)


async def test_failure_after_first_actual_booking_rolls_everything_back(bank_seed, bank_db, monkeypatch):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids)
    original = PaymentService.book
    booked = []
    async def fail_after_booking(self, payment_id):
        payment = await original(self,payment_id)
        booked.append(payment.id)
        raise RuntimeError('Injected after actual first booking')
    monkeypatch.setattr(PaymentService,'book',fail_after_booking)
    with pytest.raises(RuntimeError,match='actual first booking'):
        await api.post(f'{BASE}/{tid}/assign',json=split(ids))
    assert len(booked)==1
    assert await bank_db.scalar(text('SELECT status FROM bank_transaction WHERE id=:id'),{'id':uuid.UUID(tid)}) == 'NEU'
    assert await bank_db.scalar(text('SELECT count(*) FROM payment WHERE bank_transaction_id=:id'),{'id':uuid.UUID(tid)}) == 0
    assert await bank_db.scalar(text('SELECT count(*) FROM audit_event WHERE entity_id=:id'),{'id':booked[0]}) == 0


@pytest.mark.parametrize('other_property_payment', [False, True])
async def test_owner_soll_only_from_finalized_plan(bank_seed, bank_db, other_property_payment):
    api, ids = bank_seed
    tid = await transaction(bank_db,ids,amount='300.00',purpose='Hausgeld Juni 2025')
    plan = await api.post('/api/economic-plans',json={'property_id':str(ids['property']),'plan_year':2025,'period_start':'2025-01-01','period_end':'2025-12-31'})
    assert plan.status_code == 201, plan.text
    assert (await listing(api,ids)).json()['items'][0]['suggestions'] == []
    body={'planned_costs':[{'category_code':'GRUNDSTEUER','label':'Grundsteuer','planned_amount':'3600.00','cost_group':'UMLAGEFAEHIG'}],'reserve_contribution_total':'0.00'}
    await attest_fixture(api,'economic_plan',plan.json()['id'],body)
    result=await api.post(f"/api/economic-plans/{plan.json()['id']}/finalize",json=body)
    assert result.status_code==200,result.text
    if other_property_payment:
        await bank_db.execute(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from,mea_share) VALUES (:org,:other_unit,:owner,'2020-01-01',1000)"),ids)
        await bank_db.commit()
        other = await api.post('/api/payments',json={'bank_account_id':str(ids['other_bank']),
            'direction':'IN','amount':'500.00','value_date':'2025-06-01','payment_type':'HAUSGELD_VORSCHUSS',
            'owner_profile_id':str(ids['owner']),'period_year':2025,'period_month':6})
        assert other.status_code == 201,other.text
        booked = await api.post(f"/api/payments/{other.json()['id']}/book")
        assert booked.status_code == 200,booked.text
    suggestion=(await listing(api,ids)).json()['items'][0]['suggestions'][0]
    assert suggestion['lines'][0]['owner_profile_id']==str(ids['owner'])
    assert Decimal(suggestion['lines'][0]['amount'])==Decimal('300')
    response=await api.post(f'{BASE}/{tid}/assign',json={'lines':[{'payment_type':'HAUSGELD_VORSCHUSS','amount':'300.00','period_year':2025,'period_month':6,'owner_profile_id':str(ids['owner'])}]})
    assert response.status_code==200,response.text
    assert (await listing(api,ids,status='ZUGEORDNET')).json()['items'][0]['suggestions']==[]


async def test_pagination_filters_and_order(bank_seed,bank_db):
    api,ids=bank_seed
    tids=[await transaction(bank_db,ids) for _ in range(3)]
    first=await listing(api,ids,limit=2)
    assert first.status_code==200,first.text
    second=await listing(api,ids,limit=2,offset=first.json()['next_offset'])
    assert [row['id'] for row in first.json()['items']+second.json()['items']]==sorted(tids,reverse=True)
    assert second.json()['next_offset'] is None
    for params in ({'limit':101},{'offset':-1},{'status':'BAD'}):
        assert (await listing(api,ids,**params)).status_code==422


async def test_updated_open_items_and_derived_tenant(bank_seed, bank_db):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids)
    body = split(ids)
    for line in body['lines']:
        del line['tenant_profile_id']
    response = await api.post(f'{BASE}/{tid}/assign', json=body)
    assert response.status_code == 200, response.text
    assert all(p['tenant_profile_id'] == str(ids['tenant']) for p in response.json()['payments'])
    await transaction(bank_db, ids)
    remaining = await listing(api, ids)
    assert remaining.status_code == 200, remaining.text
    assert remaining.json()['items'][0]['suggestions'] == []


async def test_real_target_mismatches_and_foreign_target(bank_seed, bank_db, seed_data):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids)
    other_person, other_tenant, foreign_person, foreign_owner = [uuid.uuid4() for _ in range(4)]
    for person, org in [(other_person, ids['org']), (foreign_person, seed_data['org_b_id'])]:
        await bank_db.execute(text("INSERT INTO person(id,organization_id,first_name,last_name) VALUES (:id,:org,'Other','Person')"), {'id':person,'org':org})
    await bank_db.execute(text('INSERT INTO tenant_profile(id,organization_id,person_id) VALUES (:id,:org,:person)'), {'id':other_tenant,'org':ids['org'],'person':other_person})
    await bank_db.execute(text('INSERT INTO owner_profile(id,organization_id,person_id) VALUES (:id,:org,:person)'), {'id':foreign_owner,'org':seed_data['org_b_id'],'person':foreign_person})
    await bank_db.commit()
    body = split(ids)
    body['lines'][0]['tenant_profile_id'] = str(other_tenant)
    assert (await api.post(f'{BASE}/{tid}/assign',json=body)).status_code == 422
    owner_line = {'payment_type':'HAUSGELD_VORSCHUSS','amount':'850.00','period_year':2025,'period_month':6,'owner_profile_id':str(foreign_owner)}
    assert (await api.post(f'{BASE}/{tid}/assign',json={'lines':[owner_line]})).status_code == 404
    other_tid = await transaction(bank_db,ids,bank='other_bank')
    owner_line['owner_profile_id'] = str(ids['owner'])
    assert (await api.post(f'{BASE}/{other_tid}/assign',json={'lines':[owner_line]})).status_code == 422
    for lines in ([None], [split(ids)['lines'][0], None]):
        assert (await api.post(f'{BASE}/{tid}/assign',json={'lines':lines})).status_code == 422
    assert (await api.post(f'{BASE}/{tid}/assign',json={**split(ids),'direction':'OUT'})).status_code == 422


@pytest.mark.parametrize('write_grant', [False, True])
async def test_scoped_matching_and_booking_respect_exact_grants_and_units(migrator_session, scope_seed, bank_db, write_grant):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    # The existing report fixture has 500+100 Soll and 100 already paid in June.
    await bank_db.execute(text("UPDATE person SET iban='DE89370400440532013000' WHERE id=:person"),ids)
    await bank_db.commit()
    tid = await transaction(bank_db,ids,amount='500.00')
    ids['tenant'] = ids['profile']
    body = split(ids)
    body['lines'][0]['amount'] = '400.00'
    body['lines'][1]['amount'] = '100.00'
    async with scope_client(ids) as api:
        result = await listing(api,ids)
        assert result.status_code == 200,result.text
        suggestions = result.json()['items'][0]['suggestions']
        assert len(suggestions) == 1
        assert all(line['lease_agreement_id'] == str(ids['lease']) for line in suggestions[0]['lines'])
        assert all(line['lease_agreement_id'] != str(ids['excluded_lease']) for line in suggestions[0]['lines'])
        assert (await api.post(f'{BASE}/{tid}/assign',json=body)).status_code == 404
        assert (await api.post(f'{BASE}/{tid}/ignore')).status_code == 404
        if write_grant:
            await bank_db.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL"),ids)
            await bank_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:clerk,ARRAY['read','prepare','confirm'],:actor,'Book synthetic split')"),ids)
            await bank_db.commit()
            response = await api.post(f'{BASE}/{tid}/assign',json=body)
            assert response.status_code == 200,response.text
            payment_ids = [uuid.UUID(row['id']) for row in response.json()['payments']]
            bindings = (await bank_db.execute(text('SELECT scope_id FROM accounting_scope_binding WHERE payment_id=ANY(CAST(:ids AS uuid[]))'),{'ids':payment_ids})).scalars().all()
            assert bindings == [ids['scope'],ids['scope']]
    # The organization administrator has no financial grants in this fixture.
    async with scope_client(ids,'actor') as admin:
        assert (await listing(admin,ids)).status_code == 404
        result = await admin.get(BASE)
        assert result.status_code == 200,result.text
        assert result.json()['items'] == []
        assert (await admin.post(f'{BASE}/{tid}/ignore')).status_code == 404


async def test_scoped_list_restores_context_between_accounts(migrator_session,scope_seed,bank_db):
    ids = await statement_fixture(migrator_session,scope_seed,'lease')
    await bank_db.execute(text("UPDATE person SET iban='DE89370400440532013000' WHERE id=:person"),ids)
    await bank_db.commit()
    await transaction(bank_db,ids,amount='500.00')
    await transaction(bank_db,ids,amount='600.00',bank='second_bank')
    async with scope_client(ids) as api:
        response = await api.get(BASE)
        assert response.status_code==200,response.text
        assert {row['bank_account_id'] for row in response.json()['items']}=={str(ids['bank']),str(ids['second_bank'])}
        suggestions = {row['bank_account_id']:row['suggestions'] for row in response.json()['items']}
        assert suggestions[str(ids['bank'])][0]['lines'][0]['open_amount']=='400.00'
        second = suggestions[str(ids['second_bank'])]
        assert len(second) == 1
        assert second[0]['lines'][0]['lease_agreement_id'] == str(ids['excluded_lease'])
        assert second[0]['lines'][0]['open_amount'] == '700.00'


async def test_finalized_owner_period_is_not_overwritten_by_later_plan(bank_seed,bank_db):
    api,ids=bank_seed
    for year,total in [(2025,'3600.00'),(2026,'4800.00')]:
        plan=await api.post('/api/economic-plans',json={'property_id':str(ids['property']),'plan_year':year,
            'period_start':f'{year}-01-01','period_end':f'{year}-12-31'})
        assert plan.status_code==201,plan.text
        body={'planned_costs':[{'category_code':'GRUNDSTEUER','label':'Grundsteuer','planned_amount':total,'cost_group':'UMLAGEFAEHIG'}],'reserve_contribution_total':'0.00'}
        await attest_fixture(api,'economic_plan',plan.json()['id'],body)
        finalized=await api.post(f"/api/economic-plans/{plan.json()['id']}/finalize",json=body)
        assert finalized.status_code==200,finalized.text
    await transaction(bank_db,ids,amount='300.00',purpose='Hausgeld Juni 2025',value_date='2026-02-02')
    response=await listing(api,ids)
    assert response.status_code==200,response.text
    suggestion=response.json()['items'][0]['suggestions'][0]
    assert suggestion['lines'][0]['period_year']==2025
    assert suggestion['lines'][0]['period_month']==6
    assert suggestion['lines'][0]['amount']=='300.00'


async def test_explicit_all_filter_keeps_processed_rows_and_pending_default(bank_seed, bank_db):
    api, ids = bank_seed
    assigned_id = await transaction(bank_db, ids)
    ignored_id = await transaction(bank_db, ids, amount='25.00', purpose='Unbekannter Eingang')
    assert (await api.post(f'{BASE}/{assigned_id}/assign', json=split(ids))).status_code == 200
    assert (await api.post(f'{BASE}/{ignored_id}/ignore')).status_code == 200
    response = await listing(api, ids, status='ALL')
    assert response.status_code == 200, response.text
    assert {row['id']: row['status'] for row in response.json()['items']} == {
        assigned_id: 'ZUGEORDNET', ignored_id: 'IGNORIERT',
    }
    assert all(row['suggestions'] == [] for row in response.json()['items'])
    pending = await listing(api, ids)
    assert pending.status_code == 200, pending.text
    assert pending.json()['items'] == []
