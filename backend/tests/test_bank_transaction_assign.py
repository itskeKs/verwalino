"""T21: real bank matching and atomic, tenant-safe payment booking."""
import asyncio
import uuid
from decimal import Decimal
from datetime import date

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
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


async def existing_cost(api, db, ids, amount='120.00', *, property_key='property', confirm=True, scope_id=None):
    category = (await db.execute(text('SELECT id,plan_version FROM cost_category LIMIT 1'))).first()
    response = await api.post('/api/cost-entries', json={
        'property_id': str(ids[property_key]), 'accounting_scope_id': str(scope_id) if scope_id else None,
        'cost_category_id': str(category.id),
        'cost_category_plan_version': category.plan_version, 'gross_amount': amount,
        'invoice_date': '2025-06-01', 'service_period_from': '2025-01-01',
        'service_period_to': '2025-12-31', 'tenant_apportionable': 'UMLAGEFAEHIG',
        'document_number': 'Electricity instalment',
    })
    assert response.status_code == 201, response.text
    cost_id = response.json()['id']
    if confirm:
        response = await api.post(f'/api/cost-entries/{cost_id}/confirm')
        assert response.status_code == 200, response.text
    return cost_id


async def cost_open(api, cost_id):
    response = await api.get(f'/api/cost-entries/{cost_id}')
    assert response.status_code == 200, response.text
    return Decimal(response.json()['bank_open_amount'])


async def cost_assignment_graph(api, property_id, *, scope_id=None):
    """Use ordinary entry points for archive and retention regression data."""
    category = (await api.get('/api/cost-categories')).json()[0]
    response = await api.post('/api/cost-entries', json={
        'property_id': str(property_id), 'accounting_scope_id': scope_id,
        'cost_category_id': category['id'], 'cost_category_plan_version': category['plan_version'],
        'gross_amount': '120.00', 'invoice_date': '2026-02-01',
        'service_period_from': '2026-01-01', 'service_period_to': '2026-12-31',
        'tenant_apportionable': 'UMLAGEFAEHIG', 'document_number': 'Electricity instalments',
    })
    assert response.status_code == 201, response.text
    cost = response.json()['id']
    assert (await api.post(f'/api/cost-entries/{cost}/confirm')).status_code == 200
    response = await api.post('/api/bank-accounts', json={
        'property_id': str(property_id), 'accounting_scope_id': scope_id,
        'account_type': 'LAUFEND', 'label': 'Electricity instalments',
    })
    assert response.status_code == 201, response.text
    bank = response.json()['id']
    statement = b'Buchungstag;Wertstellung;Betrag;Name;Verwendungszweck\n02.02.2026;02.02.2026;-60,00;Stadtwerke;Electricity February\n02.03.2026;02.03.2026;-60,00;Stadtwerke;Electricity March\n'
    response = await api.post(f'/api/bank-accounts/{bank}/imports', params={'dry_run': 'false'},
                              files={'file': ('electricity.csv', statement, 'text/csv')})
    assert response.status_code == 200, response.text
    response = await api.get('/api/bank-transactions', params={'bank_account_id': bank})
    tids = [row['id'] for row in response.json()['items']]
    assert len(tids) == 2
    response = await api.post(f'{BASE}/{tids[0]}/assign', json={'cost_entry_id': cost})
    assert response.status_code == 200, response.text
    return cost, bank, tids


@pytest.mark.parametrize('total,debits', [('60.00', ['60.00']), ('120.00', ['60.00', '60.00']),
                                          ('1234.57', ['0.01', '1234.56'])])
async def test_cost_assignment_covers_existing_cost_once_and_retries_safely(bank_seed, bank_db, total, debits):
    api, ids = bank_seed
    cost = await existing_cost(api, bank_db, ids, total)
    remaining = Decimal(total)
    for debit in debits:
        tid = await transaction(bank_db, ids, amount=f'-{debit}')
        body = {'cost_entry_id': cost}
        response = await api.post(f'{BASE}/{tid}/assign', json=body)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'ZUGEORDNET' and response.json()['payments'] == []
        remaining -= Decimal(debit)
        assert await cost_open(api, cost) == remaining
        retry = await api.post(f'{BASE}/{tid}/assign', json=body)
        assert retry.status_code == 200, retry.text
        assert await cost_open(api, cost) == remaining
        assert (await listing(api, ids, status='ZUGEORDNET')).json()['items'][0]['cost_entry_id'] == cost
    assert await bank_db.scalar(text('SELECT count(*) FROM payment WHERE bank_account_id=:bank'), ids) == 0
    assert await bank_db.scalar(text("SELECT sum(gross_amount) FROM cost_entry WHERE property_id=:property AND status='confirmed' AND superseded_by_id IS NULL"), ids) == Decimal(total)


@pytest.mark.parametrize('amount,other_property,confirm,expected', [
    ('60.00', False, True, 422), ('0.00', False, True, 422),
    ('-60.01', False, True, 409), ('-60.00', True, True, 422),
    ('-60.00', False, False, 409),
])
async def test_invalid_cost_assignment_is_atomic(bank_seed, bank_db, amount, other_property, confirm, expected):
    api, ids = bank_seed
    cost = await existing_cost(api, bank_db, ids, '60.00',
                               property_key='other_property' if other_property else 'property', confirm=confirm)
    tid = await transaction(bank_db, ids, amount=amount)
    response = await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost})
    assert response.status_code == expected, response.text
    assert await bank_db.scalar(text('SELECT status FROM bank_transaction WHERE id=:id'), {'id': uuid.UUID(tid)}) == 'NEU'


async def test_cost_assignment_hides_foreign_targets_and_refuses_mixed_assignments(bank_seed, bank_db, client_b_admin):
    api, ids = bank_seed
    cost = await existing_cost(api, bank_db, ids)
    tid = await transaction(bank_db, ids, amount='-60.00')
    assert (await client_b_admin.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost})).status_code == 404
    for target in (cost, str(uuid.uuid4())):
        response = await client_b_admin.get(f'/api/cost-entries/{target}')
        assert response.status_code == 404
    response = await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': str(uuid.uuid4())})
    assert response.status_code == 404
    response = await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost, **split(ids)})
    assert response.status_code == 422
    foreign = uuid.uuid4()
    await bank_db.execute(text('''INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,
        cost_category_plan_version,gross_amount,invoice_date,service_period_from,service_period_to,
        tenant_apportionable,status,source)
        SELECT :id,p.organization_id,p.id,c.cost_category_id,c.cost_category_plan_version,c.gross_amount,
          c.invoice_date,c.service_period_from,c.service_period_to,c.tenant_apportionable,'needs_review','MANUAL'
        FROM cost_entry c CROSS JOIN LATERAL (SELECT id,organization_id FROM property
          WHERE organization_id<>:org LIMIT 1) p WHERE c.id=CAST(:cost AS uuid)'''), {**ids, 'id': foreign, 'cost': cost})
    # Use another organization's existing property; no foreign API data is exposed.
    await bank_db.commit()
    for processed in (False, True):
        if processed:
            assigned = await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost})
            assert assigned.status_code == 200, assigned.text
        for target in (str(foreign), str(uuid.uuid4())):
            response = await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': target})
            assert response.status_code == 404 and response.json()['detail'] == 'Kostenposition nicht gefunden.'


async def test_concurrent_cost_assignments_cannot_overcover(bank_seed, bank_db):
    api, ids = bank_seed
    cost = await existing_cost(api, bank_db, ids, '120.00')
    tids = [await transaction(bank_db, ids, amount='-80.00') for _ in range(2)]
    responses = await asyncio.gather(*(api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost}) for tid in tids))
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    assert await cost_open(api, cost) == Decimal('40.00')


async def test_concurrent_retry_covers_once_and_cannot_switch_cost_or_book_payments(bank_seed, bank_db):
    api, ids = bank_seed
    cost = await existing_cost(api, bank_db, ids)
    other = await existing_cost(api, bank_db, ids)
    tid = await transaction(bank_db, ids, amount='-60.00')
    responses = await asyncio.gather(*(api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost}) for _ in range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert await cost_open(api, cost) == 60
    assert (await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': other})).status_code == 409
    assert (await api.post(f'{BASE}/{tid}/assign', json=split(ids))).status_code == 409
    assert await bank_db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=CAST(:cost AS uuid) AND action='cost_entry.bank_transaction_assigned' AND payload_diff->>'bank_transaction_id'=:id"), {'cost': cost, 'id': tid}) == 1


async def test_covered_cost_corrections_preserve_full_chain_coverage_and_original_evidence(bank_seed, bank_db):
    api, ids = bank_seed
    original = await existing_cost(api, bank_db, ids, '75.00')
    first = await transaction(bank_db, ids, amount='-60.00')
    assert (await api.post(f'{BASE}/{first}/assign', json={'cost_entry_id': original})).status_code == 200
    costs = '/api/cost-entries'
    refused = await api.post(f'{costs}/{original}/correction', json={'gross_amount': '59.99'})
    assert refused.status_code == 409, refused.text
    assert (await api.get(f'{costs}/{original}')).json()['superseded_by_id'] is None

    corrected = await api.post(f'{costs}/{original}/correction', json={'gross_amount': '100.00'})
    assert corrected.status_code == 201, corrected.text
    successor = corrected.json()['id']
    assert Decimal(corrected.json()['bank_open_amount']) == 40
    refused = await api.patch(f'{costs}/{successor}', json={'gross_amount': '59.99'})
    assert refused.status_code == 409, refused.text
    updated = await api.patch(f'{costs}/{successor}', json={'gross_amount': '90.00'})
    assert updated.status_code == 200 and Decimal(updated.json()['bank_open_amount']) == 30
    confirmed = await api.post(f'{costs}/{successor}/confirm')
    assert confirmed.status_code == 200 and Decimal(confirmed.json()['bank_open_amount']) == 30
    assert (await api.post(f'{BASE}/{first}/assign', json={'cost_entry_id': original})).status_code == 200
    assert (await api.post(f'{BASE}/{first}/assign', json={'cost_entry_id': successor})).status_code == 409
    second = await transaction(bank_db, ids, amount='-30.00')
    assert (await api.post(f'{BASE}/{second}/assign', json={'cost_entry_id': successor})).status_code == 200
    assert (await api.post(f'{costs}/{successor}/correction', json={'gross_amount': '89.99'})).status_code == 409
    corrected_again = await api.post(f'{costs}/{successor}/correction', json={'gross_amount': '90.00'})
    assert corrected_again.status_code == 201 and Decimal(corrected_again.json()['bank_open_amount']) == 0
    latest = corrected_again.json()['id']
    assert (await api.post(f'{costs}/{latest}/confirm')).status_code == 200
    extra = await transaction(bank_db, ids, amount='-0.01')
    assert (await api.post(f'{BASE}/{extra}/assign', json={'cost_entry_id': latest})).status_code == 409
    rows = (await listing(api, ids, status='ZUGEORDNET')).json()['items']
    assert {row['id']: row['cost_entry_id'] for row in rows} == {first: original, second: successor}
    audited = (await bank_db.execute(text("""SELECT entity_id::text, payload_diff->>'bank_transaction_id'
        FROM audit_event WHERE organization_id=:org AND action='cost_entry.bank_transaction_assigned'"""), ids)).all()
    assert set(audited) == {(original, first), (successor, second)}


async def test_covered_correction_cannot_be_rejected(bank_seed, bank_db):
    api, ids = bank_seed
    original = await existing_cost(api, bank_db, ids, '120.00')
    tid = await transaction(bank_db, ids, amount='-60.00')
    assigned = await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': original})
    assert assigned.status_code == 200, assigned.text
    costs = '/api/cost-entries'
    corrected = await api.post(f'{costs}/{original}/correction', json={'gross_amount': '120.00'})
    assert corrected.status_code == 201, corrected.text
    successor = corrected.json()['id']

    refused = await api.post(f'{costs}/{successor}/reject')
    assert refused.status_code == 409, refused.text
    assert (await api.get(f'{costs}/{successor}')).json()['status'] == 'needs_review'
    assert (await api.get(f'{costs}/{original}')).json()['superseded_by_id'] == successor
    assert await bank_db.scalar(text("""SELECT count(*) FROM audit_event
        WHERE entity_id=CAST(:id AS uuid) AND action='cost_entry.rejected'"""), {'id': successor}) == 0
    confirmed = await api.post(f'{costs}/{successor}/confirm')
    assert confirmed.status_code == 200, confirmed.text
    assert await cost_open(api, successor) == 60
    report = await api.get('/api/reports/property-year', params={'property_id': str(ids['property']), 'year': 2025})
    assert report.status_code == 200, report.text
    assert Decimal(report.json()['expense_total']) == 120
    row, = (await listing(api, ids, status='ZUGEORDNET')).json()['items']
    assert (row['id'], row['cost_entry_id']) == (tid, original)
    events = (await bank_db.execute(text("""SELECT entity_id::text, payload_diff->>'bank_transaction_id'
        FROM audit_event WHERE organization_id=:org AND action='cost_entry.bank_transaction_assigned'"""), ids)).all()
    assert events == [(original, tid)]

    uncovered = await existing_cost(api, bank_db, ids)
    correction = await api.post(f'{costs}/{uncovered}/correction', json={})
    assert correction.status_code == 201, correction.text
    rejected = await api.post(f"{costs}/{correction.json()['id']}/reject")
    assert rejected.status_code == 200 and rejected.json()['status'] == 'rejected', rejected.text


async def test_correction_and_new_assignment_serialize_against_same_coverage(bank_seed, bank_db):
    api, ids = bank_seed
    cost = await existing_cost(api, bank_db, ids)
    first = await transaction(bank_db, ids, amount='-60.00')
    second = await transaction(bank_db, ids, amount='-40.00')
    assert (await api.post(f'{BASE}/{first}/assign', json={'cost_entry_id': cost})).status_code == 200
    correction, assignment = await asyncio.gather(
        api.post(f'/api/cost-entries/{cost}/correction', json={'gross_amount': '90.00'}),
        api.post(f'{BASE}/{second}/assign', json={'cost_entry_id': cost}),
    )
    assert (correction.status_code, assignment.status_code) in {(201, 409), (409, 200)}, (
        correction.text, assignment.text)
    if correction.status_code == 201:
        assert await cost_open(api, correction.json()['id']) == 30
    else:
        assert await cost_open(api, cost) == 20


async def test_database_guards_covered_successor_and_its_boundaries(bank_seed, bank_db):
    api, ids = bank_seed
    original = await existing_cost(api, bank_db, ids)
    tid = await transaction(bank_db, ids, amount='-60.00')
    assert (await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': original})).status_code == 200
    foreign_property = await existing_cost(api, bank_db, ids, property_key='other_property', confirm=False)
    with pytest.raises(IntegrityError):
        async with bank_db.begin_nested():
            await bank_db.execute(text('UPDATE cost_entry SET superseded_by_id=CAST(:next AS uuid) WHERE id=CAST(:id AS uuid)'),
                                  {'id': original, 'next': foreign_property})
    await bank_db.rollback()
    result = await api.post(f'/api/cost-entries/{original}/correction', json={'gross_amount': '60.00'})
    assert result.status_code == 201, result.text
    successor = result.json()['id']
    with pytest.raises(IntegrityError):
        async with bank_db.begin_nested():
            await bank_db.execute(text("UPDATE cost_entry SET gross_amount=59.99,status='confirmed' WHERE id=CAST(:id AS uuid)"),
                                  {'id': successor})
    await bank_db.rollback()


async def test_superseded_cost_is_not_an_assignment_target(bank_seed, bank_db):
    api, ids = bank_seed
    cost = await existing_cost(api, bank_db, ids)
    response = await api.post(f'/api/cost-entries/{cost}/correction', json={'gross_amount': '100.00'})
    assert response.status_code == 201, response.text
    tid = await transaction(bank_db, ids, amount='-60.00')
    assert (await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost})).status_code == 409


async def test_cost_assignment_requires_live_scope_write_grants_and_exact_scope(migrator_session, scope_seed, bank_db):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await bank_db.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND user_id=:clerk AND revoked_at IS NULL"), ids)
    for key in ('scope', 'second_scope'):
        await bank_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:target,:clerk,ARRAY['read','prepare','confirm'],:actor,'Assign synthetic costs')"), {**ids, 'target': ids[key]})
    await bank_db.commit()
    async with scope_client(ids) as api:
        cost = await existing_cost(api, bank_db, ids, scope_id=ids['scope'])
        other = await existing_cost(api, bank_db, ids, scope_id=ids['second_scope'])
        tid = await transaction(bank_db, ids, amount='-60.00')
        # Selecting the bank scope hides targets belonging to another scope.
        assert (await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': other})).status_code == 404
        response = await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost})
        assert response.status_code == 200, response.text
        assert await cost_open(api, cost) == 60
        await bank_db.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL"), ids)
        await bank_db.commit()
        assert (await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost})).status_code == 404
        assert (await api.get(f'/api/cost-entries/{cost}')).status_code == 404


async def test_later_scope_classification_cannot_separate_matched_bank_and_cost(bank_seed, bank_db):
    api, ids = bank_seed
    cost = await existing_cost(api, bank_db, ids)
    tid = await transaction(bank_db, ids, amount='-60.00')
    assert (await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost})).status_code == 200
    corrected = await api.post(f'/api/cost-entries/{cost}/correction', json={'gross_amount': '120.00'})
    assert corrected.status_code == 201, corrected.text
    successor = corrected.json()['id']
    assert (await api.post(f'/api/cost-entries/{successor}/confirm')).status_code == 200
    account = await api.post('/api/bank-accounts', json={'property_id': str(ids['property']),
        'account_type': 'LAUFEND', 'label': 'Second legacy account'})
    assert account.status_code == 201, account.text
    another = await transaction(bank_db, {**ids, 'bank': uuid.UUID(account.json()['id'])}, amount='-1.00')
    scopes = []
    for label in ('Cost scope', 'Other scope'):
        response = await api.post('/api/accounting-scopes', headers={'Idempotency-Key': str(uuid.uuid4())}, json={
            'property_id': str(ids['property']), 'scope_type': 'WEG', 'label': label,
            'valid_from': '2025-01-01', 'valid_to': '2025-12-31', 'whole_property': True,
        })
        assert response.status_code == 201, response.text
        sphere = response.json()['id']
        response = await api.post(f'/api/accounting-scopes/{sphere}/grants', headers={'Idempotency-Key': str(uuid.uuid4())},
            json={'user_id': str(ids['actor']), 'capabilities': ['read', 'prepare', 'confirm', 'approve', 'export'], 'reason': 'Classify matched evidence'})
        assert response.status_code == 201, response.text
        scopes.append(sphere)
    response = await api.post(f'/api/accounting-scopes/{scopes[0]}/bindings', json={
        'resource_type': 'bank_account', 'resource_id': str(ids['bank']), 'reason': 'Matched evidence'})
    assert response.status_code == 201, response.text
    incomplete = await api.post(f'{BASE}/{another}/assign', json={'cost_entry_id': successor})
    assert incomplete.status_code == 404, incomplete.text
    for sphere, expected in ((scopes[1], 422), (scopes[0], 201)):
        response = await api.post(f'/api/accounting-scopes/{sphere}/bindings', json={
            'resource_type': 'cost_entry', 'resource_id': successor, 'reason': 'Matched evidence'})
        assert response.status_code == expected, response.text
    response = await api.post(f'/api/accounting-scopes/{scopes[0]}/bindings', json={
        'resource_type': 'cost_entry', 'resource_id': cost, 'reason': 'Original matched evidence'})
    assert response.status_code == 201, response.text
    assert await cost_open(api, successor) == 60
    assert (await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost})).status_code == 200


async def test_cost_link_and_financial_bank_fields_cannot_be_rewritten(bank_seed, bank_db):
    api, ids = bank_seed
    cost = await existing_cost(api, bank_db, ids, '60.00')
    tid = await transaction(bank_db, ids, amount='-60.00')
    response = await api.post(f'{BASE}/{tid}/assign', json={'cost_entry_id': cost})
    assert response.status_code == 200, response.text
    for sql in ("UPDATE bank_transaction SET cost_entry_id=NULL", "UPDATE bank_transaction SET amount=-1",
                "UPDATE bank_transaction SET status='NEU'", 'DELETE FROM bank_transaction'):
        with pytest.raises(DBAPIError):
            async with bank_db.begin_nested():
                await bank_db.execute(text(sql + ' WHERE id=:id'), {'id': uuid.UUID(tid)})
    assert (await api.post(f'{BASE}/{tid}/ignore')).status_code == 409
    assert (await api.patch(f'/api/cost-entries/{cost}', json={'gross_amount': '1.00'})).status_code == 409


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


async def test_heating_purpose_is_suggested_and_booked_as_a_tenant_heating_advance(bank_seed, bank_db):
    api, ids = bank_seed
    changed = await api.patch(f"/api/leases/{ids['lease']}", json={'heating_advance_payment': '90.00'})
    assert changed.status_code == 200, changed.text
    tid = await transaction(bank_db, ids, amount='90.00', purpose='Heizkosten Juni 2025')
    response = await listing(api, ids)
    assert response.status_code == 200, response.text
    suggestion, = response.json()['items'][0]['suggestions']
    line, = suggestion['lines']
    assert (line['payment_type'], line['amount']) == ('HK_VORAUSZAHLUNG', '90.00')
    response = await api.post(f'{BASE}/{tid}/assign', json={'lines': [{
        'payment_type': 'HK_VORAUSZAHLUNG', 'amount': '90.00', 'period_year': 2025, 'period_month': 6,
        'lease_agreement_id': str(ids['lease']), 'tenant_profile_id': str(ids['tenant'])}]})
    assert response.status_code == 200, response.text
    payment, = response.json()['payments']
    assert payment['payment_type'] == 'HK_VORAUSZAHLUNG' and payment['status'] == 'booked'


async def test_combined_bank_assignment_books_one_advance_and_rejects_a_second_heating_line(bank_seed, bank_db):
    api, ids = bank_seed
    changed = await api.patch(f"/api/leases/{ids['lease']}", json={
        "base_rent": "750.00", "advance_mode": "COMBINED", "advance_payment": "240.00"})
    assert changed.status_code == 200, changed.text
    tid = await transaction(bank_db, ids, amount="990.00")
    result = await listing(api, ids)
    assert result.status_code == 200, result.text
    suggestion = result.json()["items"][0]["suggestions"][0]
    assert [(row["payment_type"], row["amount"]) for row in suggestion["lines"]] == [
        ("KALTMIETE", "750.00"), ("NK_VORAUSZAHLUNG", "240.00")]
    lines = [{key: row[key] for key in ("payment_type", "amount", "period_year", "period_month",
                                      "lease_agreement_id", "tenant_profile_id")} for row in suggestion["lines"]]
    refused = await api.post(f"{BASE}/{tid}/assign", json={"lines": [
        lines[0], {**lines[1], "amount": "150.00"}, {**lines[1], "payment_type": "HK_VORAUSZAHLUNG", "amount": "90.00"}]})
    assert refused.status_code == 422, refused.text
    assigned = await api.post(f"{BASE}/{tid}/assign", json={"lines": lines})
    assert assigned.status_code == 200, assigned.text
    assert [(row["payment_type"], row["amount"]) for row in assigned.json()["payments"]] == [
        ("KALTMIETE", "750.00"), ("NK_VORAUSZAHLUNG", "240.00")]


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

    async def foreign_pages():
        # Org B is session-scoped and can retain legitimate imports from other tests.
        pages = []
        for status in ('NEU', 'ALL'):
            offset = 0
            while True:
                response = await client_b_admin.get(BASE, params={'status': status, 'offset': offset})
                assert response.status_code == 200, response.text
                page = response.json()
                pages.append((status, page))
                if page['next_offset'] is None:
                    break
                assert page['next_offset'] > offset
                offset = page['next_offset']
        return pages

    before = await foreign_pages()
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
    after = await foreign_pages()
    assert after == before
    own_ids = {str(value) for value in ids.values() if isinstance(value, uuid.UUID)} | {str(tid)}
    assert all(source_id not in str(after) for source_id in own_ids)


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


@pytest.mark.parametrize('action,expected', [('assign', 'ZUGEORDNET'), ('ignore', 'IGNORIERT')])
async def test_transaction_status_committed_before_success_response(bank_seed, bank_db, app_rw_session, action, expected):
    api, ids = bank_seed
    tid = await transaction(bank_db, ids)
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {'org': str(ids['org'])})
    observed, immediate = [], []

    async def observe(scope, receive, send):
        async def observe_send(message):
            if message['type'] == 'http.response.start' and message['status'] == 200:
                observed.append(await app_rw_session.scalar(text('SELECT status FROM bank_transaction WHERE id=:id'), {'id': uuid.UUID(tid)}))
                immediate.append((await listing(api, ids, status=expected)).json()['items'])
            await send(message)
        await app(scope, receive, observe_send)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observe), base_url='http://test', cookies=api.cookies) as client:
        response = await client.post(f'{BASE}/{tid}/{action}', json=split(ids) if action == 'assign' else None)
    assert response.status_code == 200, response.text
    assert observed == [expected], 'transaction status not committed at http.response.start'
    assert [item['id'] for item in immediate[0]] == [tid]


@pytest.mark.parametrize('kind', ['unit-create', 'unit-update', 'person-create', 'person-update', 'tenant-create', 'account-create', 'account-update', 'lease-create', 'lease-update'])
async def test_named_master_data_write_visible_before_response(bank_seed, app_rw_session, kind):
    api, ids = bank_seed
    marker = f'Saved {uuid.uuid4().hex}'
    method, path, body, query = {
        'unit-create': ('POST', '/api/units', {'property_id': str(ids['property']), 'number': marker, 'usage_type': 'WOHNUNG'}, 'SELECT number FROM unit WHERE number=:marker'),
        'unit-update': ('PUT', f"/api/units/{ids['unit']}", {'number': marker}, 'SELECT number FROM unit WHERE number=:marker'),
        'person-create': ('POST', '/api/persons', {'last_name': marker}, 'SELECT last_name FROM person WHERE last_name=:marker'),
        'person-update': ('PUT', f"/api/persons/{ids['person']}", {'last_name': marker}, 'SELECT last_name FROM person WHERE last_name=:marker'),
        'tenant-create': ('POST', '/api/tenant-profiles', {'person_id': str(ids['owner_person'])}, 'SELECT CAST(person_id AS text) FROM tenant_profile WHERE person_id=CAST(:marker AS uuid)'),
        'account-create': ('POST', '/api/bank-accounts', {'property_id': str(ids['property']), 'account_type': 'LAUFEND', 'label': marker}, 'SELECT label FROM bank_account WHERE label=:marker'),
        'account-update': ('PATCH', f"/api/bank-accounts/{ids['bank']}", {'label': marker}, 'SELECT label FROM bank_account WHERE label=:marker'),
        'lease-create': ('POST', '/api/leases', {'unit_id': str(ids['unit']), 'tenant_profile_id': str(ids['tenant']), 'valid_from': '2020-01-01', 'valid_to': '2020-12-31', 'base_rent': '700.00', 'advance_payment': '150.00', 'notes': marker}, 'SELECT notes FROM lease_agreement WHERE notes=:marker'),
        'lease-update': ('PATCH', f"/api/leases/{ids['lease']}", {'notes': marker}, 'SELECT notes FROM lease_agreement WHERE notes=:marker'),
    }[kind]
    if kind == 'tenant-create':
        marker = str(ids['owner_person'])
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {'org': str(ids['org'])})
    observed = []

    async def observe(scope, receive, send):
        async def observe_send(message):
            if message['type'] == 'http.response.start' and message['status'] in (200, 201):
                observed.append(await app_rw_session.scalar(text(query), {'marker': marker}))
            await send(message)
        await app(scope, receive, observe_send)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observe), base_url='http://test', cookies=api.cookies) as client:
        response = await client.request(method, path, json=body)
    assert response.status_code in (200, 201), response.text
    assert observed == [marker], f'{kind}: saved value not committed at http.response.start'
