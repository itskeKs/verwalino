"""Read-only statements select one live accounting sphere before loading money."""
import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.conftest import MIGRATOR_URL

from app.core.config import settings
from tests.conftest import _make_session_cookie as create_session_token
from app.main import app
from tests.test_financial_scope_boundary import worker_tenant as worker_tenant, scope_seed as scope_seed
from tests.test_scope_grants import scope_fixture

pytestmark = pytest.mark.asyncio


async def statement_fixture(session, seed, kind):
    ids = await scope_fixture(session, seed)
    ids.update(person=uuid.uuid4(), profile=uuid.uuid4(), unit=uuid.uuid4(), lease=uuid.uuid4(),
               second_scope=uuid.uuid4(), second_bank=uuid.uuid4())
    if kind == 'lease':
        ids['scope'], ids['other_scope'] = ids['other_scope'], ids['scope']
    scope_type = 'MIET' if kind == 'lease' else 'WEG'
    ids['scope'] = uuid.uuid4()
    await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,valid_to,whole_property,origin,created_by) VALUES (:scope,:org,:property,:kind,'Bounded report scope','2025-01-01','2025-12-31',:whole,'REVIEWED',:actor)"), {**ids, 'kind': scope_type, 'whole': kind != 'lease'})
    await session.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Statement','Person','','','')"), ids)
    profile_table = 'tenant_profile' if kind == 'lease' else 'owner_profile'
    await session.execute(text(f"INSERT INTO {profile_table}(id,organization_id,person_id,notes) VALUES (:profile,:org,:person,'')"), ids)
    await session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit,:org,:property,'1','WOHNUNG')"), ids)
    if kind == 'lease':
        await session.execute(text('INSERT INTO accounting_scope_unit(organization_id,scope_id,unit_id,property_id) VALUES (:org,:scope,:unit,:property)'), ids)
        ids.update(excluded_unit=uuid.uuid4(), excluded_lease=uuid.uuid4())
        await session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:excluded_unit,:org,:property,'Excluded','WOHNUNG')"), ids)
        await session.execute(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES (:excluded_lease,:org,:excluded_unit,:profile,'2024-01-01',700,100,'')"), ids)
        await session.execute(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES (:lease,:org,:unit,:profile,'2024-01-01',500,100,'')"), ids)
    await session.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:second_scope,:org,:property,:kind,'Second statement sphere','2025-01-01',true,'REVIEWED',:actor)"), {**ids, 'kind': scope_type})
    await session.execute(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:second_bank,:org,:property,'LAUFEND','Second account')"), ids)
    # Replace the preparation grant with read-only grants: reports must not demand write access.
    await session.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant"), ids)
    for sphere in ('scope', 'other_scope', 'second_scope'):
        await session.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:sphere,:clerk,ARRAY['read'],:actor,'Read statements')"), {**ids, 'sphere': ids[sphere]})
    for bank, sphere, amount in [('bank','scope',100), ('second_bank','second_scope',900)]:
        params = {**ids, 'bank_id': ids[bank], 'sphere': ids[sphere], 'amount': amount, 'payment': uuid.uuid4(),
                  'type': 'KALTMIETE' if kind == 'lease' else 'HAUSGELD_VORSCHUSS',
                  'tenant': ids['profile'] if kind == 'lease' else None,
                  'owner': ids['profile'] if kind == 'owner' else None,
                  'lease_id': ids['lease'] if kind == 'lease' else None}
        await session.execute(text("""INSERT INTO payment(id,organization_id,property_id,bank_account_id,amount,direction,value_date,payment_type,tenant_profile_id,owner_profile_id,lease_agreement_id,period_year,period_month,status)
          VALUES (:payment,:org,:property,:bank_id,:amount,'IN','2025-06-01',:type,:tenant,:owner,:lease_id,2025,6,'booked')"""), params)
        await session.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:sphere,:bank_id,'REVIEWED','Statement fixture',:actor)"), params)
        await session.execute(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:sphere,:payment,:bank_id,'REVIEWED','Statement fixture',:actor)"), params)
    await session.commit()
    return ids


async def mutate(statement, ids):
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(statement, ids)
    finally:
        await engine.dispose()


async def fetch(statement, ids):
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            return await connection.scalar(text(statement), ids)
    finally:
        await engine.dispose()


def client(ids, actor='clerk'):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
        cookies={settings.session_cookie_name: create_session_token(ids[actor], ids['org'])})


def url(ids, kind):
    subject = ids['lease'] if kind == 'lease' else ids['profile']
    return f'/api/payments/mietkonto/{kind}/{subject}'


def query(ids):
    return {'period_start': '2025-01-01', 'period_end': '2025-12-31', 'accounting_scope_id': str(ids['scope'])}


@pytest.mark.parametrize('kind', ['lease', 'owner'])
async def test_explicit_read_scope_selects_only_its_money(migrator_session, scope_seed, kind):
    ids = await statement_fixture(migrator_session, scope_seed, kind)
    async with client(ids) as api:
        response = await api.get(url(ids, kind), params=query(ids))
        assert response.status_code == 200, response.text
        # A lease's debt counts the payments of every scope (the 900.00 of the second one too); an owner's
        # Hausgeld account stays the one scope's own.
        assert response.json()['ist_total'] == ('1000.00' if kind == 'lease' else '100.00')
        assert response.json()['soll_total'] == ('7200.00' if kind == 'lease' else '0.00')
        legacy = {k: v for k, v in query(ids).items() if k != 'accounting_scope_id'}
        assert (await api.get(url(ids, kind), params=legacy)).status_code == 409
        if kind == 'lease':
            items = await api.get(f"/api/payments/open-items/lease/{ids['lease']}", params=query(ids))
            assert items.status_code == 200, items.text
            # 7200.00 owed - 1000.00 paid in both scopes; the 500.00 June's rent did not need settle the oldest
            # position, so no credit is listed and the listed positions add up to the debt (#806).
            assert sum(Decimal(row['open_amount']) for row in items.json()) == Decimal('6200.00')
            assert all(Decimal(row['open_amount']) > 0 for row in items.json())
            assert (await api.get(f"/api/payments/mietkonto/lease/{ids['excluded_lease']}", params=query(ids))).status_code == 404
            bulk = await api.get('/api/payments/open-items', params=query(ids))
            assert bulk.status_code == 200, bulk.text
            assert [row['lease_agreement_id'] for row in bulk.json()] == [str(ids['lease'])]
            assert (await api.get('/api/payments/open-items', params=legacy)).status_code == 409


@pytest.mark.parametrize('kind', ['lease', 'owner'])
async def test_scope_report_requires_correct_type_and_effective_period(migrator_session, scope_seed, kind):
    ids = await statement_fixture(migrator_session, scope_seed, kind)
    async with client(ids) as api:
        for overrides in [{'accounting_scope_id': str(ids['other_scope'])}, {'period_start': '2024-01-01'}, {'period_end': '2024-12-31'}, {'period_end': '2026-01-01'}]:
            response = await api.get(url(ids, kind), params={**query(ids), **overrides})
            assert response.status_code == 422, response.text


@pytest.mark.parametrize('kind', ['lease', 'owner'])
async def test_report_rechecks_live_grant_and_never_uses_admin_fallback(migrator_session, scope_seed, kind):
    ids = await statement_fixture(migrator_session, scope_seed, kind)
    async with client(ids, 'actor') as admin:
        assert (await admin.get(url(ids, kind), params=query(ids))).status_code == 404
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    async with client(ids) as api:
        assert (await api.get(url(ids, kind), params=query(ids))).status_code == 404


@pytest.mark.parametrize('kind', ['lease', 'owner'])
async def test_report_blocks_unclassified_financial_evidence(migrator_session, scope_seed, kind):
    ids = await statement_fixture(migrator_session, scope_seed, kind)
    await mutate(text("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unclassified')"), ids)
    async with client(ids) as api:
        response = await api.get(url(ids, kind), params=query(ids))
        assert response.status_code == 409, response.text

async def test_owner_plan_inheritance_and_explicit_scope_must_agree(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'owner')
    ids['plan'] = uuid.uuid4()
    await mutate(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES (:plan,:org,:property,2025,'2025-01-01','2025-06-30','draft','fixture')"), ids)
    await mutate(text("INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,economic_plan_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:plan,'REVIEWED','Exact plan fixture',:actor)"), ids)
    async with client(ids) as api:
        params = {**query(ids), 'economic_plan_id': str(ids['plan']), 'period_end': '2025-06-30'}
        implicit = {key: value for key, value in params.items() if key != 'accounting_scope_id'}
        for selected in [params, implicit]:
            result = await api.get(url(ids, 'owner'), params=selected)
            assert result.status_code == 200, result.text
            assert result.json()['ist_total'] == '100.00'
        assert (await api.get(url(ids, 'owner'), params={**params, 'accounting_scope_id': str(ids['second_scope'])})).status_code == 404
        assert (await api.get(url(ids, 'owner'), params={**params, 'period_end': '2025-12-31'})).status_code == 422
        assert (await api.get(f"/api/payments/mietkonto/owner/{uuid.uuid4()}", params=params)).status_code == 409


# ---------------------------------------------------------------------------
# The debt of a lease counts every booked payment of that lease, whichever scope's account received it (#803).
# The lease of `statement_fixture(.., 'lease')` owes 12 x 600.00 in 2025 (500.00 rent and 100.00 advance); in June
# 100.00 was paid into the account of `scope` and 900.00 into the account of `second_scope`.
# ---------------------------------------------------------------------------
LEASE_FIELDS = ('open_items', 'soll_total', 'ist_total', 'balance', 'due_balance', 'not_yet_due_balance', 'dunning_notices')


def scoped_query(ids, key):
    return {**query(ids), 'accounting_scope_id': str(ids[key])}


async def add_payments(ids, *rows):
    """Payment rows straight into the table, each bound to the scope its row names (default: the second scope).

    The write guards are off for the inserts: a fixture has no booking actor. A row may name the original it
    `reverses`, and the row it `reversed_by`, which is set last because the two reference each other.
    """
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text('ALTER TABLE payment DISABLE TRIGGER USER'))
            for row in rows:
                params = {**ids, 'payment': row.get('id') or uuid.uuid4(), 'bank_id': row.get('bank') or ids['second_bank'],
                          'sphere': row.get('scope') or ids['second_scope'], 'amount': row['amount'],
                          'direction': row.get('direction', 'IN'), 'type': row.get('type', 'KALTMIETE'),
                          'year': row.get('year', 2025), 'month': row.get('month', 6), 'status': row.get('status', 'booked'),
                          'value_date': row.get('value_date', date(2025, 6, 1)), 'lease_id': row.get('lease', ids['lease']),
                          'reverses': row.get('reverses'), 'property_id': row.get('property', ids['property'])}
                await connection.execute(text("""INSERT INTO payment(id,organization_id,property_id,bank_account_id,amount,direction,value_date,payment_type,
                    tenant_profile_id,lease_agreement_id,period_year,period_month,status,reverses_id)
                  VALUES (:payment,:org,:property_id,:bank_id,:amount,:direction,:value_date,:type,:profile,:lease_id,:year,:month,:status,:reverses)"""), params)
                if row.get('scope', True) is not None:
                    await connection.execute(text("""INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by)
                      VALUES (:org,:property_id,:sphere,:payment,:bank_id,'REVIEWED','Statement fixture',:actor)"""), params)
            for row in rows:
                if row.get('reversed_by'):
                    await connection.execute(text('UPDATE payment SET reversed_by_id=:reversed_by WHERE id=:id'), {'id': row['id'], 'reversed_by': row['reversed_by']})
            await connection.execute(text('ALTER TABLE payment ENABLE TRIGGER USER'))
    finally:
        await engine.dispose()


async def statements(api, ids, keys=('scope', 'second_scope')):
    """The rent account of the lease as every named scope shows it."""
    answers = {}
    for key in keys:
        answer = await api.get(url(ids, 'lease'), params=scoped_query(ids, key))
        assert answer.status_code == 200, answer.text
        answers[key] = answer.json()
    return answers


@pytest.mark.parametrize('selected,elsewhere', [('scope', '900.00'), ('second_scope', '100.00')])
async def test_the_debt_of_a_lease_counts_the_payments_of_every_scope(migrator_session, scope_seed, selected, elsewhere):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    async with client(ids) as api:
        (statement,) = (await statements(api, ids, (selected,))).values()
        # 12 x 600.00 owed, 100.00 + 900.00 paid: 6.200,00 open, whichever scope is read; the payments of the
        # other scope are one summed amount.
        assert (statement['soll_total'], statement['ist_total'], statement['balance']) == ('7200.00', '1000.00', '6200.00')
        assert statement['other_scope_ist'] == elsewhere
        # 11 months of 500.00 rent, June's 500.00 rent overpaid by 500.00, 12 x 100.00 advance.
        assert sum(Decimal(row['open_amount']) for row in statement['open_items']) == Decimal('6200.00')
        items = await api.get(f"/api/payments/open-items/lease/{ids['lease']}", params=scoped_query(ids, selected))
        assert items.status_code == 200, items.text
        assert sum(Decimal(row['open_amount']) for row in items.json()) == Decimal('6200.00')
        bulk = await api.get('/api/payments/open-items', params=scoped_query(ids, selected))
        assert bulk.status_code == 200, bulk.text
        # The second scope covers the whole property, the other lease of the tenant too.
        owed = {row['lease_agreement_id']: sum(Decimal(item['open_amount']) for item in row['items']) for row in bulk.json()}
        assert owed[str(ids['lease'])] == Decimal('6200.00')


async def test_every_scope_that_may_open_a_lease_shows_the_same_open_amount_and_so_does_a_read_without_scope(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    # The same lease terms and the same two payments on a property that has no accounting scope at all.
    ids.update(twin_property=uuid.uuid4(), twin_unit=uuid.uuid4(), twin_lease=uuid.uuid4(), twin_bank=uuid.uuid4())
    for statement in (
        "INSERT INTO property(id,organization_id,name,street,zip_code,city,management_type) VALUES (:twin_property,:org,'Twin','','','','MIET')",
        "INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:twin_unit,:org,:twin_property,'1','WOHNUNG')",
        "INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES (:twin_lease,:org,:twin_unit,:profile,'2024-01-01',500,100,'')",
        "INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:twin_bank,:org,:twin_property,'LAUFEND','Twin account')",
    ):
        await mutate(text(statement), ids)
    for amount in ('100.00', '900.00'):
        await add_payments(ids, {'amount': amount, 'scope': None, 'bank': ids['twin_bank'], 'lease': ids['twin_lease'], 'property': ids['twin_property']})
    async with client(ids) as api:
        scoped = await statements(api, ids)
        unscoped = await api.get(f"/api/payments/mietkonto/lease/{ids['twin_lease']}", params={'period_start': '2025-01-01', 'period_end': '2025-12-31'})
        assert unscoped.status_code == 200, unscoped.text
        first, second = (({key: value for key, value in body.items() if key != 'other_scope_ist'}) for body in scoped.values())
        assert first == second
        assert {key: unscoped.json()[key] for key in LEASE_FIELDS} == {key: first[key] for key in LEASE_FIELDS}
        assert unscoped.json()['other_scope_ist'] is None


async def test_a_member_without_a_grant_for_the_other_scope_gets_its_payments_only_as_one_summed_amount(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    await mutate(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:second_scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    hidden = await fetch('SELECT id::text FROM payment WHERE bank_account_id=:second_bank', ids)
    async with client(ids) as api:
        responses = [await api.get(path, params=scoped_query(ids, 'scope')) for path in (
            url(ids, 'lease'), f"/api/payments/open-items/lease/{ids['lease']}")]
        responses.append(await api.get('/api/payments/open-items', params=scoped_query(ids, 'scope')))
        for response in responses:
            assert response.status_code == 200, response.text
            # Neither the payment, nor its account, nor its date, nor the account's name reaches the caller.
            for secret in (hidden, str(ids['second_bank']), str(ids['second_scope']), 'Second account', 'Second statement sphere', '2025-06-01'):
                assert secret not in response.text, secret
        statement = responses[0].json()
        assert set(statement) == {*LEASE_FIELDS, 'other_scope_ist'}
        assert (statement['ist_total'], statement['other_scope_ist'], statement['balance']) == ('1000.00', '900.00', '6200.00')
        # The scope itself stays closed to this member.
        assert (await api.get(url(ids, 'lease'), params=scoped_query(ids, 'second_scope'))).status_code == 404


@pytest.mark.parametrize('refunded', ['scope', 'second_scope'])
async def test_a_refund_to_the_tenant_reduces_what_was_paid_in_whichever_scope_it_was_booked(migrator_session, scope_seed, refunded):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    bank = {'scope': ids['bank'], 'second_scope': ids['second_bank']}[refunded]
    await add_payments(ids, {'amount': '150.00', 'direction': 'OUT', 'bank': bank, 'scope': ids[refunded]})
    async with client(ids) as api:
        statements_by_scope = await statements(api, ids)
    for statement in statements_by_scope.values():
        # 100.00 + 900.00 paid in, 150.00 paid back: 850.00 paid, 7.200,00 - 850,00 open.
        assert (statement['ist_total'], statement['balance']) == ('850.00', '6350.00')
    # What each scope's own account took in, less the refund where it was booked, is what the other view sums up.
    summed = {'scope': {'scope': '900.00', 'second_scope': '-50.00'}, 'second_scope': {'scope': '750.00', 'second_scope': '100.00'}}[refunded]
    assert {key: statement['other_scope_ist'] for key, statement in statements_by_scope.items()} == summed


@pytest.mark.parametrize('booked_in', ['scope', 'second_scope'])
async def test_what_a_scope_does_not_count_stays_out_whichever_scope_it_was_booked_in(migrator_session, scope_seed, booked_in):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    bank = {'scope': ids['bank'], 'second_scope': ids['second_bank']}[booked_in]
    where = {'bank': bank, 'scope': ids[booked_in]}
    original, storno = uuid.uuid4(), uuid.uuid4()
    await add_payments(
        ids,
        {**where, 'amount': '777.00', 'status': 'draft'},                                              # not booked
        {**where, 'id': original, 'amount': '333.00', 'reversed_by': storno},                          # a reversed payment ...
        {**where, 'id': storno, 'amount': '333.00', 'direction': 'OUT', 'reverses': original},         # ... and its reversal
        {**where, 'amount': '3000.00', 'type': 'KAUTION'},                                             # deposit
        {**where, 'amount': '555.00', 'lease': ids['excluded_lease']},                                 # another lease of the same tenant
    )
    # A Hausgeld payment of a WEG scope on the same mixed-use property never reduces rent debt.
    ids.update(owner_person=uuid.uuid4(), owner=uuid.uuid4(), weg_bank=uuid.uuid4(), weg_payment=uuid.uuid4())
    for statement in (
        "INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:owner_person,:org,'Hausgeld','Owner','','','')",
        "INSERT INTO owner_profile(id,organization_id,person_id,notes) VALUES (:owner,:org,:owner_person,'')",
        "INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:weg_bank,:org,:property,'LAUFEND','Hausgeld account')",
        "INSERT INTO payment(id,organization_id,property_id,bank_account_id,amount,direction,value_date,payment_type,owner_profile_id,period_year,period_month,status) VALUES (:weg_payment,:org,:property,:weg_bank,666,'IN','2025-06-01','HAUSGELD_VORSCHUSS',:owner,2025,6,'booked')",
        "INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:other_scope,:weg_bank,'REVIEWED','Hausgeld fixture',:actor)",
        "INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:other_scope,:weg_payment,:weg_bank,'REVIEWED','Hausgeld fixture',:actor)",
    ):
        await mutate(text(statement), ids)
    async with client(ids) as api:
        statements_by_scope = await statements(api, ids)
        for key, statement in statements_by_scope.items():
            assert (statement['ist_total'], statement['balance']) == ('1000.00', '6200.00'), key
        assert {key: statement['other_scope_ist'] for key, statement in statements_by_scope.items()} == {'scope': '900.00', 'second_scope': '100.00'}
        # The payments of another lease of the tenant do not open that lease in the scope that excludes its unit.
        assert (await api.get(f"/api/payments/mietkonto/lease/{ids['excluded_lease']}", params=query(ids))).status_code == 404


@pytest.mark.parametrize('period_start,period_end,soll,ist,elsewhere', [
    ('2025-01-01', '2025-05-31', '3000.00', '0.00', None),         # June is outside: neither payment counts
    ('2025-06-01', '2025-06-30', '600.00', '1000.00', '900.00'),   # June only: both count, the other scope's one summed
])
async def test_a_payment_assigned_to_a_month_outside_the_requested_period_is_not_counted(
        migrator_session, scope_seed, period_start, period_end, soll, ist, elsewhere):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    async with client(ids) as api:
        statement = await api.get(url(ids, 'lease'), params={**scoped_query(ids, 'scope'), 'period_start': period_start, 'period_end': period_end})
        assert statement.status_code == 200, statement.text
        body = statement.json()
        assert (body['soll_total'], body['ist_total'], body['other_scope_ist']) == (soll, ist, elsewhere)
        assert Decimal(body['balance']) == Decimal(soll) - Decimal(ist)


async def test_a_booked_payment_of_the_lease_without_any_scope_counts_like_one_of_another_scope(migrator_session, scope_seed):
    ids = await statement_fixture(migrator_session, scope_seed, 'lease')
    # A legacy payment of the lease on an account of another property that no scope classifies: the selected scope
    # cannot read it, and it is booked money of the lease all the same.
    ids['legacy_bank'] = uuid.uuid4()
    await mutate(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label) VALUES (:legacy_bank,:org,:other_property,'LAUFEND','Legacy account')"), ids)
    await add_payments(ids, {'amount': '250.00', 'scope': None, 'bank': ids['legacy_bank'], 'property': ids['other_property']})
    async with client(ids) as api:
        statements_by_scope = await statements(api, ids)
    assert {key: (body['ist_total'], body['other_scope_ist'], body['balance']) for key, body in statements_by_scope.items()} == {
        'scope': ('1250.00', '1150.00', '5950.00'), 'second_scope': ('1250.00', '350.00', '5950.00')}
