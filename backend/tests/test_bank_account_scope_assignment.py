"""A bank account saved without an Abrechnungskreis can be assigned one afterwards (#671).

The calculation guard refuses a scope while any bank account of its property is
unclassified, ended accounts included. The refusal is a structured German error
that names the account; the existing classification route repairs it.
"""
import uuid

import pytest
from sqlalchemy import text

from tests.settlement_period_support import period_data_cleanup  # noqa: F401

# Classified finance makes an organization answer no unscoped statement read, so every test releases its records.
pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures('period_data_cleanup')]


async def weg_with_open_account(admin, seed_data, *, account_label='Laufendes Konto', account_type='LAUFEND'):
    prop = (await admin.post('/api/properties', json={
        'name': 'Assignment WEG', 'street': 'Test 1', 'zip_code': '12345', 'city': 'Test', 'management_type': 'WEG'})).json()['id']
    scope = (await admin.post('/api/accounting-scopes', json={
        'property_id': prop, 'scope_type': 'WEG', 'label': 'WEG Assignment', 'valid_from': '2025-01-01',
        'whole_property': True, 'units': []}, headers={'Idempotency-Key': str(uuid.uuid4())})).json()
    grant = await admin.post(f"/api/accounting-scopes/{scope['id']}/grants", json={
        'user_id': str(seed_data['user_a_admin_id']), 'capabilities': ['read', 'prepare'],
        'reason': 'Explicit setup grant'}, headers={'Idempotency-Key': str(uuid.uuid4())})
    assert grant.status_code == 201, grant.text
    account = await admin.post('/api/bank-accounts', json={
        'property_id': prop, 'account_type': account_type, 'label': account_label})
    assert account.status_code == 201, account.text
    return prop, scope, account.json()


async def book_receipt(session, seed_data, prop, account):
    """A legacy receipt on the unclassified account; returns its id."""
    person, owner, payment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ids = {'org': seed_data['org_a_id'], 'property': uuid.UUID(prop), 'bank': uuid.UUID(account['id']),
           'person': person, 'owner': owner, 'payment': payment}
    await session.execute(text("INSERT INTO person(id,organization_id,last_name,street,zip_code,city) VALUES (:person,:org,'Posting','','','')"), ids)
    await session.execute(text('INSERT INTO owner_profile(id,organization_id,person_id) VALUES (:owner,:org,:person)'), ids)
    await session.execute(text("""INSERT INTO payment(id,organization_id,property_id,bank_account_id,direction,amount,value_date,
        payment_type,owner_profile_id,period_year,period_month,status)
        VALUES (:payment,:org,:property,:bank,'IN',100,'2025-06-01','HAUSGELD_VORSCHUSS',:owner,2025,6,'booked')"""), ids)
    await session.commit()
    return payment


async def calculation_attempt(admin, prop, scope):
    """The levy list enters the same read context as every scoped calculation."""
    return await admin.get('/api/special-levies', params={'accounting_scope_id': scope['id'], 'property_id': prop})


async def assign(admin, scope, account, reason='Konto gehört zu dieser WEG'):
    return await admin.post(f"/api/accounting-scopes/{scope['id']}/bindings", json={
        'resource_type': 'bank_account', 'resource_id': account['id'], 'reason': reason})


async def test_refusal_names_the_unclassified_account_in_german(client_a_admin, seed_data):
    prop, scope, account = await weg_with_open_account(client_a_admin, seed_data)
    refused = await calculation_attempt(client_a_admin, prop, scope)
    assert refused.status_code == 409, refused.text
    detail = refused.json()['detail']
    assert detail['code'] == 'SCOPE_ACCOUNT_UNCLASSIFIED'
    assert '„Laufendes Konto“' in detail['message']
    assert 'Abrechnungskreis' in detail['message'] and 'Bankkonten' in detail['message']
    assert 'Review unclassified' not in detail['message']


async def test_assigning_the_scope_removes_the_refusal(client_a_admin, seed_data):
    prop, scope, account = await weg_with_open_account(client_a_admin, seed_data)
    assert (await calculation_attempt(client_a_admin, prop, scope)).status_code == 409
    bound = await assign(client_a_admin, scope, account)
    assert bound.status_code == 201, bound.text
    assert (await calculation_attempt(client_a_admin, prop, scope)).status_code == 200


async def test_ended_account_counts_until_it_is_assigned(client_a_admin, seed_data):
    prop, scope, account = await weg_with_open_account(client_a_admin, seed_data)
    ended = await client_a_admin.patch(f"/api/bank-accounts/{account['id']}", json={'is_active': False})
    assert ended.status_code == 200, ended.text
    refused = await calculation_attempt(client_a_admin, prop, scope)
    assert refused.status_code == 409 and refused.json()['detail']['code'] == 'SCOPE_ACCOUNT_UNCLASSIFIED'
    assert (await assign(client_a_admin, scope, account)).status_code == 201
    assert (await calculation_attempt(client_a_admin, prop, scope)).status_code == 200


async def test_refusal_counts_further_unclassified_accounts(client_a_admin, seed_data):
    prop, scope, first = await weg_with_open_account(client_a_admin, seed_data, account_label='A Girokonto')
    for label in ('B Rücklage', 'C Festgeld'):
        assert (await client_a_admin.post('/api/bank-accounts', json={
            'property_id': prop, 'account_type': 'LAUFEND', 'label': label})).status_code == 201
    message = (await calculation_attempt(client_a_admin, prop, scope)).json()['detail']['message']
    assert '„A Girokonto“' in message and '2 weitere' in message
    assert 'B Rücklage' not in message


async def test_other_unclassified_inputs_are_refused_in_german_without_naming_an_account(client_a_admin, seed_data, migrator_session):
    prop, scope, account = await weg_with_open_account(client_a_admin, seed_data)
    await book_receipt(migrator_session, seed_data, prop, account)
    assert (await assign(client_a_admin, scope, account)).status_code == 201
    refused = await calculation_attempt(client_a_admin, prop, scope)
    assert refused.status_code == 409, refused.text
    detail = refused.json()['detail']
    assert detail['code'] == 'SCOPE_INPUTS_UNCLASSIFIED'
    assert 'Review unclassified' not in detail['message'] and 'Abrechnungskreis' in detail['message']


async def test_account_with_postings_is_assigned_without_rewriting_them(client_a_admin, seed_data, migrator_session):
    prop, scope, account = await weg_with_open_account(client_a_admin, seed_data)
    payment = await book_receipt(migrator_session, seed_data, prop, account)
    assert (await assign(client_a_admin, scope, account)).status_code == 201
    # The receipt keeps its original booking and is offered to the legacy-payment review of the scope.
    preview = await client_a_admin.get(f"/api/accounting-scopes/{scope['id']}/legacy-payments")
    assert preview.status_code == 200, preview.text
    assert [row['id'] for row in preview.json()['items']] == [str(payment)]


async def test_account_list_shows_the_assigned_scope(client_a_admin, seed_data):
    prop, scope, account = await weg_with_open_account(client_a_admin, seed_data)
    before = next(row for row in (await client_a_admin.get('/api/bank-accounts', params={'property_id': prop})).json() if row['id'] == account['id'])
    assert before['accounting_scope_label'] is None
    assert (await assign(client_a_admin, scope, account)).status_code == 201
    after = next(row for row in (await client_a_admin.get('/api/bank-accounts', params={'property_id': prop})).json() if row['id'] == account['id'])
    assert after['accounting_scope_label'] == 'WEG Assignment'


async def test_foreign_account_or_scope_answers_not_found(client_a_admin, client_b_admin, seed_data):
    prop, scope, account = await weg_with_open_account(client_a_admin, seed_data)
    foreign_prop = (await client_b_admin.post('/api/properties', json={
        'name': 'Foreign', 'street': 'Test 2', 'zip_code': '12345', 'city': 'Test', 'management_type': 'WEG'})).json()['id']
    foreign_account = (await client_b_admin.post('/api/bank-accounts', json={
        'property_id': foreign_prop, 'account_type': 'LAUFEND', 'label': 'Foreign account'})).json()
    # Organization B reaches neither the scope nor the account of organization A.
    assert (await assign(client_b_admin, scope, account)).status_code == 404
    # Organization A cannot classify the account of organization B into its own scope.
    assert (await assign(client_a_admin, scope, foreign_account)).status_code == 404
    assert (await assign(client_b_admin, scope, foreign_account)).status_code == 404
