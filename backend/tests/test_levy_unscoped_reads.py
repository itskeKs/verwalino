"""An organization with a special levy answers unscoped reads and exports like classified data does (#722, #739, #816, #840)."""
import csv
import importlib
import io
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import APP_RW_URL, MIGRATOR_SYNC_URL
from tests.test_financial_scope_boundary import actor, scope_seed as scope_seed
from tests.test_invoice_extraction_worker import worker_tenant as worker_tenant
from tests.test_property_source_enforcement import synthetic_enforcement
from tests.test_scope_payment_reconciliation import read_value
from tests.test_special_levy import client, create, levy_db as levy_db, levy_fixture as levy_fixture, new_owner, query

pytestmark = pytest.mark.asyncio

PERIOD = {'period_start': '2025-01-01', 'period_end': '2025-12-31'}
SCOPE_REQUIRED = ('Wählen Sie einen eindeutigen Abrechnungskreis und prüfen Sie noch nicht zugeordnete Daten, '
                  'bevor Sie die Zahlungen berechnen.')
MONITOR_SCOPE_REQUIRED = ('The organization-wide deadline monitor is unavailable for classified financial data; '
                          'a scoped view is required')
READS = ('context', 'fristen', 'open_items', 'mietkonto_lease', 'open_items_lease', 'mietkonto_owner',
         'mietkonto_owner_plan', 'dunning_owner', 'dunning_tenant')
SCOPE_REQUIRED_READS = tuple(name for name in READS if name not in ('context', 'fristen'))


async def add_lease_and_plan(db, ids):
    """A tenant of the levy's property and an economic plan bound to no accounting scope."""
    ids.update(tenant_person=uuid.uuid4(), tenant=uuid.uuid4(), lease=uuid.uuid4(), plan=uuid.uuid4())
    await db.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:tenant_person,:org,'Levy','Tenant','','','')"), ids)
    await db.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:tenant,:org,:tenant_person,'')"), ids)
    await db.execute(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES (:lease,:org,:unit,:tenant,'2025-01-01',500,100,'')"),
                     {**ids, 'unit': ids['units'][0]})
    await db.execute(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES (:plan,:org,:other_property,2025,'2025-01-01','2025-12-31','draft','test')"), ids)
    await db.commit()


async def unscoped_reads(api, ids):
    """Every unscoped read of an organization-wide overview or of the levy's own property, as (status, body) pairs."""
    owner = str(ids['owners'][0])
    answers = {
        'context': await api.get('/api/fristen/context'),
        'fristen': await api.get('/api/fristen', params={'reference_date': '2025-12-31'}),
        'open_items': await api.get('/api/payments/open-items', params=PERIOD),
        'mietkonto_lease': await api.get(f"/api/payments/mietkonto/lease/{ids['lease']}", params=PERIOD),
        'open_items_lease': await api.get(f"/api/payments/open-items/lease/{ids['lease']}", params=PERIOD),
        'mietkonto_owner': await api.get(f'/api/payments/mietkonto/owner/{owner}', params=PERIOD),
        'mietkonto_owner_plan': await api.get(f'/api/payments/mietkonto/owner/{owner}',
                                              params={**PERIOD, 'economic_plan_id': str(ids['plan'])}),
        'dunning_owner': await api.post('/api/dunning/owner', json={
            'owner_profile_id': owner, **PERIOD, 'reference_date': '2025-12-31'}),
        'dunning_tenant': await api.post('/api/dunning/tenant', json={
            'tenant_profile_id': str(ids['tenant']), 'lease_agreement_id': str(ids['lease']),
            'period_start': '2025-06-01', 'period_end': '2025-06-30', 'reference_date': '2025-07-15'}),
    }
    assert answers.keys() == set(READS)
    return {name: (answer.status_code, answer.json()) for name, answer in answers.items()}


def refused_with_a_scope_required(answers):
    assert answers['context'] == (200, {'requires_accounting_scope': True})
    assert answers['fristen'] == (409, {'detail': MONITOR_SCOPE_REQUIRED})
    for name in SCOPE_REQUIRED_READS:
        assert answers[name] == (409, {'detail': SCOPE_REQUIRED}), name


async def test_levy_does_not_change_the_answer_of_any_unscoped_overview_read(levy_db, levy_fixture):
    ids = levy_fixture
    await add_lease_and_plan(levy_db, ids)
    async with client(ids) as api:
        classified = await unscoped_reads(api, ids)
        refused_with_a_scope_required(classified)
        levy = await create(api, ids)
        assert await unscoped_reads(api, ids) == classified
        # The scope that holds the levy is read as before.
        shares = await api.get(f"/api/payments/mietkonto/owner/{ids['owners'][0]}",
                               params={**PERIOD, **query(ids), 'accounting_scope_id': str(ids['scope'])})
        assert shares.status_code == 200, shares.text
        assert shares.json()['soll_total'] == '3000.00'
        assert (await api.get('/api/special-levies', params=query(ids))).json() == [levy]


async def test_caller_without_access_to_the_levy_scope_still_gets_no_levy_data(levy_db, levy_fixture, client_b_admin):
    ids = levy_fixture
    await add_lease_and_plan(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids)
    await levy_db.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    await levy_db.commit()
    async with client(ids) as blind:
        answers = await unscoped_reads(blind, ids)
        refused_with_a_scope_required(answers)
        for name, (_, body) in answers.items():
            assert levy['id'] not in str(body) and '3000.00' not in str(body), name
        # The scope itself and its levies stay as hidden as before.
        for url, params in (('/api/special-levies', query(ids)), ('/api/special-levies', {}),
                            (f"/api/payments/mietkonto/owner/{ids['owners'][0]}", {**PERIOD, **query(ids)})):
            assert (await blind.get(url, params=params)).status_code == 404, url
    assert (await client_b_admin.get('/api/special-levies', params=query(ids))).status_code == 404


async def test_enforced_sources_keep_refusing_an_unscoped_read_with_the_plain_404(levy_db, levy_fixture, scope_seed):
    ids = levy_fixture
    await add_lease_and_plan(levy_db, ids)
    async with client(ids) as api:
        await create(api, ids)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        answers = await unscoped_reads(api, ids)
        # An economic plan on another property is itself hidden from this caller, which is a different refusal.
        for name in set(READS) - {'mietkonto_owner_plan'}:
            assert answers[name] == (404, {'detail': 'Complete report sources not accessible'}), name


async def add_property_without_scope(db, ids):
    """On the property without an accounting scope: a tenant with a lease, an owner, and an owner who also holds
    a unit of the levy's property."""
    ids.update(b_units=[uuid.uuid4() for _ in range(3)], b_person=uuid.uuid4(), b_tenant=uuid.uuid4(), b_lease=uuid.uuid4(),
               b_owner_person=uuid.uuid4(), b_owner=uuid.uuid4())
    for n, unit in enumerate(ids['b_units']):
        await db.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type,mea) VALUES (:unit,:org,:other_property,:number,'WOHNUNG',100)"),
                         {**ids, 'unit': unit, 'number': f'B{n + 1}'})
    await db.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:b_person,:org,'Plain','Tenant','','','')"), ids)
    await db.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:b_tenant,:org,:b_person,'')"), ids)
    await db.execute(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES (:b_lease,:org,:b_unit,:b_tenant,'2025-01-01',500,100,'')"),
                     {**ids, 'b_unit': ids['b_units'][0]})
    await db.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:b_owner_person,:org,'Plain','Owner','','','')"), ids)
    await db.execute(text("INSERT INTO owner_profile(id,organization_id,person_id,notes) VALUES (:b_owner,:org,:b_owner_person,'')"), ids)
    await db.execute(text("INSERT INTO ownership_history(id,organization_id,unit_id,owner_profile_id,valid_from) VALUES (gen_random_uuid(),:org,:unit,:b_owner,'2024-01-01')"),
                     {**ids, 'unit': ids['b_units'][1]})
    # The levy's owner Berta also holds a unit of the property without a scope.
    await db.execute(text("INSERT INTO ownership_history(id,organization_id,unit_id,owner_profile_id,valid_from) VALUES (gen_random_uuid(),:org,:unit,:owner,'2024-01-01')"),
                     {**ids, 'unit': ids['b_units'][2], 'owner': ids['owners'][1]})
    await db.commit()


async def reads_without_scope(api, ids):
    """The unscoped reads that concern one lease or one owner of the property without a scope, as (status, body) pairs."""
    both = str(ids['owners'][1])
    answers = {
        'mietkonto_lease': await api.get(f"/api/payments/mietkonto/lease/{ids['b_lease']}", params=PERIOD),
        'open_items_lease': await api.get(f"/api/payments/open-items/lease/{ids['b_lease']}", params=PERIOD),
        'mietkonto_owner': await api.get(f"/api/payments/mietkonto/owner/{ids['b_owner']}", params=PERIOD),
        'dunning_owner': await api.post('/api/dunning/owner', json={
            'owner_profile_id': str(ids['b_owner']), **PERIOD, 'reference_date': '2025-12-31'}),
        # Berta holds units of both properties: the levy's property is one the read concerns.
        'mietkonto_owner_on_both': await api.get(f'/api/payments/mietkonto/owner/{both}', params=PERIOD),
        'dunning_owner_on_both': await api.post('/api/dunning/owner', json={
            'owner_profile_id': both, **PERIOD, 'reference_date': '2025-12-31'}),
    }
    return {name: (answer.status_code, answer.json()) for name, answer in answers.items()}


def rent_of_the_property_without_scope(answers):
    """What the reads must show whether or not the organization holds a levy: 12 months of 500 rent and 100 advance, nothing paid."""
    status, account = answers['mietkonto_lease']
    assert status == 200 and (account['soll_total'], account['ist_total'], account['balance']) == ('7200.00', '0.00', '7200.00'), account
    status, items = answers['open_items_lease']
    assert status == 200 and len(items) == 24 and sum(float(item['open_amount']) for item in items) == 7200.0, items
    assert {item['payment_type'] for item in items} == {'KALTMIETE', 'NK_VORAUSZAHLUNG'}
    status, owner = answers['mietkonto_owner']
    assert status == 200 and (owner['soll_total'], owner['ist_total']) == ('0.00', '0.00'), owner
    for name in ('mietkonto_owner_on_both', 'dunning_owner_on_both'):
        assert answers[name] == (409, {'detail': SCOPE_REQUIRED}), name


async def test_levy_leaves_the_reads_and_the_notice_of_a_property_without_a_scope_as_before(levy_db, levy_fixture):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    async with client(ids) as api:
        before = await reads_without_scope(api, ids)
        rent_of_the_property_without_scope(before)
        await create(api, ids)
        assert await reads_without_scope(api, ids) == before
        # June rent of 500 and 100 advance is unpaid: the tenant notice is a first reminder over 600.00.
        notice = await api.post('/api/dunning/tenant', json={
            'tenant_profile_id': str(ids['b_tenant']), 'lease_agreement_id': str(ids['b_lease']),
            'period_start': '2025-06-01', 'period_end': '2025-06-30', 'reference_date': '2025-07-15'})
        assert notice.status_code == 201, notice.text
        assert (notice.json()['level'], notice.json()['principal_amount'], notice.json()['total_amount']) == (1, '600.00', '600.00')
        assert (await api.get(f"/api/dunning/{notice.json()['id']}")).status_code == 200


async def test_levy_still_refuses_what_concerns_the_levy_property_or_the_whole_organization(levy_db, levy_fixture):
    ids = levy_fixture
    await add_lease_and_plan(levy_db, ids)
    await add_property_without_scope(levy_db, ids)
    async with client(ids) as api:
        await create(api, ids)
        # The overviews of the organization and every read of the levy's property, a lease of it included.
        refused_with_a_scope_required(await unscoped_reads(api, ids))
        # The Mahnlauf reads every lease of the organization, the one without a scope included.
        batch = await api.post('/api/dunning/batch/preview', json={**PERIOD, 'reference_date': '2025-12-31', 'threshold': '1.00'})
        assert (batch.status_code, batch.json()) == (409, {'detail': SCOPE_REQUIRED})


async def test_unknown_and_foreign_ids_answer_as_before_the_levy(levy_db, levy_fixture, seed_data):
    ids = levy_fixture
    foreign = {key: uuid.uuid4() for key in ('property', 'unit', 'person', 'tenant', 'lease')}
    org_b = seed_data['org_b_id']
    await levy_db.execute(text("INSERT INTO property(id,organization_id,name,street,zip_code,city,management_type) VALUES (:property,:org,'Foreign','','','','MISCH')"), {**foreign, 'org': org_b})
    await levy_db.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type,mea) VALUES (:unit,:org,:property,'F1','WOHNUNG',100)"), {**foreign, 'org': org_b})
    await levy_db.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:person,:org,'Foreign','Tenant','','','')"), {**foreign, 'org': org_b})
    await levy_db.execute(text("INSERT INTO tenant_profile(id,organization_id,person_id,notes) VALUES (:tenant,:org,:person,'')"), {**foreign, 'org': org_b})
    await levy_db.execute(text("INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment,notes) VALUES (:lease,:org,:unit,:tenant,'2025-01-01',500,100,'')"), {**foreign, 'org': org_b})
    await levy_db.commit()
    unknown = uuid.uuid4()

    async def answers(api):
        result = {}
        for name, lease in (('unknown', unknown), ('foreign', foreign['lease'])):
            for kind in ('mietkonto', 'open-items'):
                answer = await api.get(f'/api/payments/{kind}/lease/{lease}', params=PERIOD)
                result[(name, kind)] = (answer.status_code, answer.json())
            notice = await api.post('/api/dunning/tenant', json={
                'tenant_profile_id': str(foreign['tenant'] if name == 'foreign' else unknown), 'lease_agreement_id': str(lease),
                'period_start': '2025-06-01', 'period_end': '2025-06-30', 'reference_date': '2025-07-15'})
            result[(name, 'notice')] = (notice.status_code, notice.json())
        return result

    async with client(ids) as api:
        before = await answers(api)
        assert {key: status for key, (status, _) in before.items()} == {key: 404 for key in before}
        await create(api, ids)
        assert await answers(api) == before


async def test_caller_without_access_to_the_levy_scope_reads_the_property_without_a_scope_without_levy_data(levy_db, levy_fixture):
    ids = levy_fixture
    await add_lease_and_plan(levy_db, ids)
    await add_property_without_scope(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids)
    await levy_db.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    await levy_db.commit()
    async with client(ids) as blind:
        answers = await reads_without_scope(blind, ids)
        rent_of_the_property_without_scope(answers)
        for name, (_, body) in answers.items():
            assert levy['id'] not in str(body) and '3000.00' not in str(body), name
        # The levy's own property stays as refused and as hidden as before.
        refused_with_a_scope_required(await unscoped_reads(blind, ids))


async def add_bank_account_without_scope(db, ids):
    """The bank account of the property without a scope, bound to no accounting scope."""
    ids.update(b_bank=uuid.uuid4())
    await db.execute(text("INSERT INTO bank_account(id,organization_id,property_id,account_type,label,iban) VALUES (:b_bank,:org,:other_property,'LAUFEND','Musterhaus','DE02200505501015871393')"), ids)
    await db.commit()


async def add_year_books_without_scope(db, ids):
    """Booked for 2025 on the property without a scope: June rent of 500.00 and an advance of 100.00, confirmed property tax of 250.00."""
    await add_bank_account_without_scope(db, ids)
    for kind, amount in (('KALTMIETE', 500), ('NK_VORAUSZAHLUNG', 100)):
        await db.execute(text("""INSERT INTO payment(id,organization_id,property_id,bank_account_id,amount,direction,value_date,payment_type,tenant_profile_id,period_year,period_month,status)
            VALUES (gen_random_uuid(),:org,:other_property,:b_bank,:amount,'IN','2025-06-03',:kind,:b_tenant,2025,6,'booked')"""), {**ids, 'kind': kind, 'amount': amount})
    category = await db.scalar(text("SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"))
    ids.update(b_category=category)
    await db.execute(text("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,cost_category_plan_version,invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source,confirmed_at,confirmed_by)
        VALUES (gen_random_uuid(),:org,:other_property,:category,1,'2025-06-01','2025-01-01','2025-12-31',250,'UMLAGEFAEHIG','confirmed','MANUAL',now(),:actor)"""), {**ids, 'category': category})
    await db.commit()


async def add_statement_line_without_scope(db, ids):
    """A bank statement line of 600.00 on that account: the tenant of the property pays rent and advance for June 2025."""
    await add_bank_account_without_scope(db, ids)
    ids.update(b_line=uuid.uuid4())
    await db.execute(text("UPDATE person SET iban='DE89370400440532013000' WHERE id=:b_person"), ids)
    await db.execute(text("""INSERT INTO bank_transaction(id,organization_id,bank_account_id,booking_date,value_date,amount,counterparty_name,counterparty_iban,purpose,import_hash,status)
        VALUES (:b_line,:org,:b_bank,'2025-06-03','2025-06-03',600,'Plain Tenant','DE89370400440532013000','Warmmiete Juni 2025',:hash,'NEU')"""),
                     {**ids, 'hash': uuid.uuid4().hex})
    await db.commit()


async def annual_overview(api, ids, key='other_property'):
    answer = await api.get('/api/reports/property-year', params={'property_id': str(ids[key]), 'year': 2025})
    return answer.status_code, answer.json()


async def bank_matching(api, ids):
    answer = await api.get('/api/bank-transactions', params={'bank_account_id': str(ids['b_bank'])})
    return answer.status_code, answer.json()


def figures_before_the_levy(answer):
    """The annual overview of the property without a scope: 600.00 income (500.00 rent, 100.00 advance), 250.00 property tax."""
    status, body = answer
    assert status == 200, body
    assert body['accounting_scope_id'] is None
    assert [(row['payment_type'], row['amount']) for row in body['income']] == [('KALTMIETE', '500.00'), ('NK_VORAUSZAHLUNG', '100.00')]
    assert [(row['category_code'], row['amount']) for row in body['expenses']] == [('GRUNDSTEUER', '250.00')]
    assert (body['income_total'], body['expense_total'], body['balance']) == ('600.00', '250.00', '350.00')


def suggestion_before_the_levy(ids, answer):
    """The statement line is suggested as the June rent of the tenant: 500.00 rent and 100.00 advance of the one lease."""
    status, body = answer
    assert status == 200, body
    line, = body['items']
    suggestion, = line['suggestions']
    assert suggestion['confidence'] == 'HIGH'
    assert [(part['payment_type'], part['amount'], part['lease_agreement_id']) for part in suggestion['lines']] == [
        ('KALTMIETE', '500.00', str(ids['b_lease'])), ('NK_VORAUSZAHLUNG', '100.00', str(ids['b_lease']))]


async def test_levy_leaves_the_annual_overview_of_a_property_without_a_scope_as_before(levy_db, levy_fixture):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_year_books_without_scope(levy_db, ids)
    async with client(ids) as api:
        before = await annual_overview(api, ids)
        figures_before_the_levy(before)
        await create(api, ids)
        assert await annual_overview(api, ids) == before
        summary = await api.get('/api/reports/property-year/anlage-v', params={'property_id': str(ids['other_property']), 'year': 2025})
        assert summary.status_code == 200, summary.text


async def test_levy_leaves_the_bank_matching_of_an_account_without_a_scope_as_before(levy_db, levy_fixture):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_statement_line_without_scope(levy_db, ids)
    async with client(ids) as api:
        before = await bank_matching(api, ids)
        suggestion_before_the_levy(ids, before)
        await create(api, ids)
        assert await bank_matching(api, ids) == before


async def add_scope_only_property(db, ids):
    """A property with an accounting scope of its own and no classified finance."""
    ids.update(d_property=uuid.uuid4(), d_scope=uuid.uuid4())
    await db.execute(text("INSERT INTO property(id,organization_id,name,street,zip_code,city,management_type) VALUES (:d_property,:org,'Scope only','','','','MISCH')"), ids)
    await db.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:d_scope,:org,:d_property,'MIET','Scope only','2025-01-01',true,'REVIEWED',:actor)"), ids)
    await db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:d_scope,:clerk,ARRAY['read'],:actor,'Scope only property')"), ids)
    await db.commit()


async def add_levy_only_property(api, db, ids):
    """A second WEG property whose only classified finance is its own frozen levy: no bank account, no booking, nothing bound."""
    ids.update(c_property=uuid.uuid4(), c_scope=uuid.uuid4(), c_unit=uuid.uuid4(), c_person=uuid.uuid4(), c_owner=uuid.uuid4(),
               c_meeting=uuid.uuid4(), c_resolution=uuid.uuid4())
    await db.execute(text("INSERT INTO property(id,organization_id,name,street,zip_code,city,management_type) VALUES (:c_property,:org,'Levy only','','','','WEG')"), ids)
    await db.execute(text("INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by) VALUES (:c_scope,:org,:c_property,'WEG','Levy only','2025-01-01',true,'REVIEWED',:actor)"), ids)
    await db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:c_scope,:clerk,ARRAY['read','prepare','confirm'],:actor,'Levy only property')"), ids)
    await db.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type,mea) VALUES (:c_unit,:org,:c_property,'1','WOHNUNG',1000)"), ids)
    await db.execute(text("INSERT INTO person(id,organization_id,first_name,last_name,street,zip_code,city) VALUES (:c_person,:org,'Levy','Only','','','')"), ids)
    await db.execute(text("INSERT INTO owner_profile(id,organization_id,person_id,notes) VALUES (:c_owner,:org,:c_person,'')"), ids)
    await db.execute(text("INSERT INTO ownership_history(id,organization_id,unit_id,owner_profile_id,valid_from) VALUES (gen_random_uuid(),:org,:c_unit,:c_owner,'2024-01-01')"), ids)
    await db.execute(text("INSERT INTO owners_meeting(id,organization_id,property_id,meeting_date,location,meeting_type,status) VALUES (:c_meeting,:org,:c_property,'2025-05-15','Test','ORDENTLICH','HELD')"), ids)
    await db.execute(text("INSERT INTO resolution(id,organization_id,meeting_id,wording,votes_yes,votes_no,votes_abstain,result) VALUES (:c_resolution,:org,:c_meeting,'Dachreparatur',2,0,0,'ANGENOMMEN')"), ids)
    await db.commit()
    await create(api, ids, property_id=str(ids['c_property']), accounting_scope_id=str(ids['c_scope']),
                 resolution_id=str(ids['c_resolution']), amount='1200.00')


async def test_levy_still_asks_for_a_scope_where_the_property_is_classified_or_has_a_scope(levy_db, levy_fixture):
    ids = levy_fixture
    await add_lease_and_plan(levy_db, ids)
    await add_property_without_scope(levy_db, ids)
    await add_year_books_without_scope(levy_db, ids)
    await add_scope_only_property(levy_db, ids)
    async with client(ids) as api:
        await add_levy_only_property(api, levy_db, ids)
        await create(api, ids)
        # The levy's own properties, one of them with the levy as its only classified finance.
        for key in ('property', 'c_property'):
            assert await annual_overview(api, ids, key) == (409, {'detail': 'Select an exact accounting scope for this property'}), key
        # A property that has a scope of its own is read through it, as before this change.
        assert await annual_overview(api, ids, 'd_property') == (409, {'detail': SCOPE_REQUIRED})
        scoped = await api.get('/api/reports/property-year', params={'property_id': str(ids['d_property']), 'year': 2025, 'accounting_scope_id': str(ids['d_scope'])})
        assert scoped.status_code == 200, scoped.text
        # The overviews of the whole organization.
        refused_with_a_scope_required(await unscoped_reads(api, ids))
        # The property without a scope is read.
        figures_before_the_levy(await annual_overview(api, ids))


async def test_caller_without_access_to_the_levy_scope_reads_the_overview_and_matching_of_the_property_without_a_scope(levy_db, levy_fixture):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_year_books_without_scope(levy_db, ids)
    await add_statement_line_without_scope(levy_db, ids)
    async with client(ids) as api:
        levy = await create(api, ids)
    await levy_db.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
    await levy_db.commit()
    async with client(ids) as blind:
        overview, matching = await annual_overview(blind, ids), await bank_matching(blind, ids)
        for answer in (overview, matching):
            assert levy['id'] not in str(answer) and '3000.00' not in str(answer) and '1200.00' not in str(answer)
        figures_before_the_levy(overview)
        # Without the scope nothing of the levy's property is shown either: 404 for the caller, no figures.
        assert (await blind.get('/api/reports/property-year', params={'property_id': str(ids['property']), 'year': 2025,
                                                                      'accounting_scope_id': str(ids['scope'])})).status_code == 404
        assert (await annual_overview(blind, ids, 'property'))[0] == 409


async def test_foreign_organization_reads_neither_the_overview_nor_the_matching_of_the_property_without_a_scope(levy_db, levy_fixture, client_b_admin):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_year_books_without_scope(levy_db, ids)
    await add_statement_line_without_scope(levy_db, ids)
    async with client(ids) as api:
        await create(api, ids)
    overview = await client_b_admin.get('/api/reports/property-year', params={'property_id': str(ids['other_property']), 'year': 2025})
    assert (overview.status_code, overview.json()) == (404, {'detail': 'Property not found'})
    matching = await client_b_admin.get('/api/bank-transactions', params={'bank_account_id': str(ids['b_bank'])})
    assert (matching.status_code, matching.json()) == (404, {'detail': 'Bank account not found'})
    for answer in (overview, matching):
        assert '600.00' not in answer.text and '250.00' not in answer.text


async def test_enforced_sources_keep_refusing_an_unscoped_read_of_the_property_without_a_scope(levy_db, levy_fixture, scope_seed):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    async with client(ids) as api:
        await create(api, ids)
    assert await property_answer(ids, subject=ids['other_property'])
    # An enforced organization refuses every unscoped read by itself, so the caller answers the plain 404, not the levy's 409.
    async with synthetic_enforcement(scope_seed):
        assert not await property_answer(ids, subject=ids['other_property'])
        assert not await property_answer(ids, capability='prepare', subject=ids['other_property'])


async def test_report_scope_source_access_answers_for_one_lease_or_owner(levy_db, levy_fixture, seed_data):
    ids = levy_fixture
    await add_lease_and_plan(levy_db, ids)
    await add_property_without_scope(levy_db, ids)
    async with client(ids) as api:
        await create(api, ids)
    sql = "SELECT report_scope_source_access(:org,:sphere,:capability,:lease,:owner)"

    async def answer(*, sphere=None, capability='read', lease=None, owner=None, org=None):
        params = {**ids, 'org': org or ids['org'], 'sphere': sphere, 'capability': capability, 'lease': lease, 'owner': owner}
        return await read_value(sql, params, actor_key='clerk')

    assert not await read_value("SELECT report_scope_source_access(:org,NULL,'read')", ids, actor_key='clerk')
    # One lease or one owner of the property without a scope: as before the levy.
    for capability in ('read', 'prepare'):
        assert await answer(capability=capability, lease=ids['b_lease'])
        assert await answer(capability=capability, owner=ids['b_owner'])
    # A corrected entry is no ownership: a mistaken entry on the levy's property does not make it one the owner touches.
    await levy_db.execute(text("INSERT INTO ownership_history(id,organization_id,unit_id,owner_profile_id,valid_from,valid_to,corrected_at,corrected_by) VALUES (gen_random_uuid(),:org,:unit,:b_owner,'2024-01-01','2024-06-01',now(),:actor)"),
                          {**ids, 'unit': ids['units'][2]})
    assert await uncommitted_answer(levy_db, ids, owner=ids['b_owner'])
    await levy_db.rollback()
    # An id that concerns no property of the organization (unknown, or of another organization) concerns no levy either.
    assert await answer(lease=uuid.uuid4()) and await answer(owner=uuid.uuid4())
    # The levy's property is one the read concerns, whichever way it is reached.
    assert not await answer(lease=ids['lease'])
    assert not await answer(owner=ids['owners'][0])
    assert not await answer(owner=ids['owners'][1])
    # No subject, or two, is no single lease or owner.
    assert not await answer() and not await answer(lease=ids['b_lease'], owner=ids['b_owner'])
    # A scope is answered as before: the subject is not looked at.
    assert await answer(sphere=ids['scope'], lease=ids['lease']) == await read_value(
        "SELECT report_scope_source_access(:org,:scope,'read')", ids, actor_key='clerk')
    # A caller of another organization gets no exemption for the levy organization's lease.
    engine = create_async_engine(APP_RW_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                     {'org': str(seed_data['org_b_id']), 'user': str(seed_data['user_b_admin_id'])})
            assert not await connection.scalar(text(sql), {**ids, 'sphere': None, 'capability': 'read', 'lease': ids['b_lease'], 'owner': None})
    finally:
        await engine.dispose()
    # An unknown capability is refused by the function it delegates to.
    assert not await answer(capability='delete', lease=ids['b_lease'])


async def function_answer(ids, *, actor_key='clerk', **arguments):
    """The five-argument function as the runtime role, for a member of the organization; unnamed arguments are NULL."""
    params = {**ids, 'sphere': None, 'capability': 'read', 'lease': None, 'owner': None, **arguments}
    return await read_value("SELECT report_scope_source_access(:org,:sphere,:capability,:lease,:owner)", params, actor_key=actor_key)


async def uncommitted_answer(db, ids, **arguments):
    """The five-argument function as the runtime role inside the open transaction of `db`, so that a corrected
    ownership row it must see is never committed: 0190 refuses to discard corrected rows, and a later downgrade
    rehearsal of the same session (test_m7_dunning) would fail on it. The caller rolls the transaction back."""
    await actor(db, ids)
    try:
        return await db.scalar(text("SELECT report_scope_source_access(:org,NULL,'read',:lease,:owner)"),
                               {'org': ids['org'], 'lease': None, 'owner': None, **arguments})
    finally:
        await db.execute(text('RESET ROLE'))


async def property_answer(ids, *, sphere=None, capability='read', lease=None, owner=None, subject=None, org=None, actor_key='clerk'):
    """The six-argument function as the runtime role, for a member of the organization; `subject` is the one property of the read."""
    params = {**ids, 'org': org or ids['org'], 'sphere': sphere, 'capability': capability, 'lease': lease, 'owner': owner, 'subject': subject}
    return await read_value("SELECT report_scope_source_access(:org,:sphere,:capability,:lease,:owner,:subject)", params, actor_key=actor_key)


async def test_report_scope_source_access_answers_for_one_property(levy_db, levy_fixture, seed_data):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_scope_only_property(levy_db, ids)
    async with client(ids) as api:
        await add_levy_only_property(api, levy_db, ids)
        await create(api, ids)
    assert not await read_value("SELECT report_scope_source_access(:org,NULL,'read')", ids, actor_key='clerk')
    # The property without a scope and without classified finance: as before the levy.
    for capability in ('read', 'prepare'):
        assert await property_answer(ids, capability=capability, subject=ids['other_property'])
    # An id that concerns no property of the organization (unknown, or of another organization) concerns no levy either.
    assert await property_answer(ids, subject=uuid.uuid4())
    assert await property_answer(ids, subject=seed_data['org_b_id'])
    # The levy's property, one whose only classified finance is the levy, and one with a scope of its own are refused.
    for key in ('property', 'c_property', 'd_property'):
        assert not await property_answer(ids, subject=ids[key]), key
    # No subject, or the property next to a lease or an owner, is no single subject.
    assert not await property_answer(ids)
    assert not await property_answer(ids, subject=ids['other_property'], lease=ids['b_lease'])
    assert not await property_answer(ids, subject=ids['other_property'], owner=ids['b_owner'])
    # A scope is answered as before: the subject is not looked at.
    assert await property_answer(ids, sphere=ids['scope'], subject=ids['other_property']) == await read_value(
        "SELECT report_scope_source_access(:org,:scope,'read')", ids, actor_key='clerk')
    # A caller of another organization gets no exemption for the levy organization's property.
    engine = create_async_engine(APP_RW_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                     {'org': str(seed_data['org_b_id']), 'user': str(seed_data['user_b_admin_id'])})
            assert not await connection.scalar(text("SELECT report_scope_source_access(:org,NULL,'read',NULL,NULL,:subject)"),
                                               {'org': ids['org'], 'subject': ids['other_property']})
    finally:
        await engine.dispose()
    # An unknown capability is refused by the function it delegates to.
    assert not await property_answer(ids, capability='delete', subject=ids['other_property'])


async def test_report_scope_source_access_refuses_an_owner_reached_only_through_a_payment_or_a_plan_line(levy_db, levy_fixture):
    ids = levy_fixture
    async with client(ids) as api:
        await create(api, ids)
    paid, planned = await new_owner(levy_db, ids), await new_owner(levy_db, ids)
    ids.update(plan_of_levy_property=uuid.uuid4())
    # Neither owner holds a unit or any other link: a read of them concerns no property, so the levy does not refuse it.
    assert await function_answer(ids, owner=paid) and await function_answer(ids, owner=planned)
    # A booked payment on the levy's property is the only link of the first owner (a payment-only recipient).
    await levy_db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:clerk,true)"),
                          {'org': str(ids['org']), 'clerk': str(ids['clerk'])})
    await levy_db.execute(text("""INSERT INTO payment(id,organization_id,property_id,bank_account_id,direction,amount,value_date,
        payment_type,owner_profile_id,period_year,period_month,status)
        VALUES (gen_random_uuid(),:org,:property,:bank,'IN',100,'2025-06-15','SONDERZAHLUNG',:owner,2025,6,'booked')"""),
                          {**ids, 'owner': paid})
    await levy_db.commit()
    assert not await function_answer(ids, owner=paid)
    assert await function_answer(ids, owner=planned)
    # A line of an economic plan of the levy's property is the only link of the second owner.
    await levy_db.execute(text("INSERT INTO economic_plan(id,organization_id,property_id,plan_year,period_start,period_end,status,engine_version) VALUES (:plan_of_levy_property,:org,:property,2025,'2025-01-01','2025-12-31','draft','test')"), ids)
    await levy_db.execute(text("INSERT INTO economic_plan_line(organization_id,economic_plan_id,line_type,owner_profile_id,label,planned_amount,monthly_amount,sort_order) VALUES (:org,:plan_of_levy_property,'OWNER_ADVANCE',:owner,'Plan line',120,10,1)"),
                          {**ids, 'owner': planned})
    await levy_db.commit()
    assert not await function_answer(ids, owner=planned)
    assert not await function_answer(ids, owner=paid)


async def test_report_scope_source_access_refuses_an_owner_reached_only_through_a_levy_allocation(levy_db, levy_fixture):
    ids = levy_fixture
    async with client(ids) as api:
        await create(api, ids)
    owner = ids['owners'][0]
    assert not await function_answer(ids, owner=owner)
    # The entry of the owner is corrected (0190): it is no ownership any more, but the levy still carries the owner's share.
    await levy_db.execute(text("UPDATE ownership_history SET corrected_at=now(),corrected_by=:actor WHERE id=:history"),
                          {**ids, 'history': ids['histories'][0]})
    assert await levy_db.scalar(text("SELECT count(*) FROM ownership_history WHERE organization_id=:org AND owner_profile_id=:owner AND corrected_at IS NULL"),
                                {**ids, 'owner': owner}) == 0
    assert await levy_db.scalar(text("SELECT count(*) FROM special_levy_allocation WHERE organization_id=:org AND owner_profile_id=:owner"),
                                {**ids, 'owner': owner}) == 1
    assert not await uncommitted_answer(levy_db, ids, owner=owner)
    await levy_db.rollback()
    assert await levy_db.scalar(text("SELECT count(*) FROM ownership_history WHERE organization_id=:org AND corrected_at IS NOT NULL"), ids) == 0


async def test_report_scope_source_access_answers_a_scoped_call_for_an_unclassified_subject_as_the_three_argument_function(levy_db, levy_fixture):
    from tests.contact_source_fixtures import grant_property_contacts
    from tests.test_property_source_access import grant
    ids = levy_fixture
    seed = {'org_a_id': ids['org'], 'user_a_admin_id': ids['actor']}
    await add_property_without_scope(levy_db, ids)
    await levy_db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:scope,:actor,ARRAY['read','prepare'],:actor,'Source boundary test')"), ids)
    await levy_db.commit()
    async with client(ids, 'actor') as api:
        await create(api, ids)
        await grant_property_contacts(api, seed, ids['property'])
        await grant(api, seed, ids['property'], caps=['read', 'prepare'])
        # Enforced sources refuse every unscoped read, as before the levy, and answer a scoped read from its scope. The
        # subjects sit on the property without a scope, which has no classified finance, so only the guard on the
        # scope keeps the five-argument function from answering the scoped call as the unscoped one.
        async with synthetic_enforcement(seed):
            for capability in ('read', 'prepare'):
                of_the_scope = await read_value("SELECT report_scope_source_access(:org,:scope,:capability)", {**ids, 'capability': capability}, actor_key='actor')
                assert of_the_scope is True
                for subject in ({'lease': ids['b_lease']}, {'owner': ids['b_owner']}):
                    assert await function_answer(ids, actor_key='actor', capability=capability, **subject) is False
                    assert await function_answer(ids, actor_key='actor', sphere=ids['scope'], capability=capability, **subject) == of_the_scope


async def test_the_property_overload_has_the_attributes_and_acl_of_the_function_it_extends(levy_db):
    old, new = 'report_scope_source_access(uuid,uuid,text)', 'report_scope_source_access(uuid,uuid,text,uuid,uuid,uuid)'
    metadata = text("""SELECT p.prosecdef, p.provolatile::text, p.proconfig, l.lanname, p.prorettype::regtype::text,
        pg_get_userbyid(p.proowner), (SELECT array_agg(CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END ORDER BY a.grantee)
          FROM aclexplode(p.proacl) a WHERE a.privilege_type='EXECUTE')
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=CAST(:signature AS regprocedure)""")
    before, after = [tuple((await levy_db.execute(metadata, {'signature': signature})).one()) for signature in (old, new)]
    assert after == before
    assert after[:5] == (True, 'v', ['search_path=pg_catalog, pg_temp', 'row_security=off'], 'plpgsql', 'boolean')
    assert sorted(after[6]) == sorted([after[5], 'app_rw']), after
    assert await levy_db.scalar(text("SELECT has_function_privilege('app_rw',CAST(:signature AS regprocedure),'EXECUTE')"), {'signature': new})


def test_0193_adds_and_drops_only_the_overload():
    migration = importlib.import_module('migrations.versions.0193_levy_scope_reach')
    successor = importlib.import_module('migrations.versions.0199_levy_property_reach')
    functions = text("""SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid),p.proacl::text,p.proconfig,p.proowner
        FROM pg_proc p WHERE p.pronamespace='public'::regnamespace ORDER BY 1""")
    new = 'report_scope_source_access(uuid,uuid,text,uuid,uuid)'
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                # 0199 replaces the overload: 0193 is measured on the schema it left, not on the head.
                successor.downgrade()
                head = {row[0]: tuple(row[1:]) for row in db.execute(functions)}
                assert new in head
                migration.downgrade()
                assert {row[0]: tuple(row[1:]) for row in db.execute(functions)} == {key: value for key, value in head.items() if key != new}
                for kept in ('report_scope_source_access(uuid,uuid,text)', 'report_sources_before_special_levy(uuid,uuid,text)'):
                    assert db.scalar(text('SELECT to_regprocedure(:signature)'), {'signature': kept}) is not None
                migration.upgrade()
                assert {row[0]: tuple(row[1:]) for row in db.execute(functions)} == head
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


def test_0199_replaces_the_overload_and_downgrade_restores_the_function_of_0193():
    migration = importlib.import_module('migrations.versions.0199_levy_property_reach')
    earlier = importlib.import_module('migrations.versions.0193_levy_scope_reach')
    functions = text("""SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid),p.proacl::text,p.proconfig,p.proowner
        FROM pg_proc p WHERE p.pronamespace='public'::regnamespace ORDER BY 1""")
    old, new = 'report_scope_source_access(uuid,uuid,text,uuid,uuid)', 'report_scope_source_access(uuid,uuid,text,uuid,uuid,uuid)'
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                head = {row[0]: tuple(row[1:]) for row in db.execute(functions)}
                assert new in head and old not in head
                migration.downgrade()
                restored = {row[0]: tuple(row[1:]) for row in db.execute(functions)}
                assert old in restored and new not in restored
                assert {key: value for key, value in restored.items() if key != old} == {key: value for key, value in head.items() if key != new}
                # The restored function is the one 0193 creates, text and ACL included.
                earlier.downgrade()
                earlier.upgrade()
                assert {row[0]: tuple(row[1:]) for row in db.execute(functions)} == restored
                migration.upgrade()
                assert {row[0]: tuple(row[1:]) for row in db.execute(functions)} == head
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


DOWNLOADS = ('property_year_csv', 'anlage_v_csv', 'datev_sources', 'datev_preview', 'datev_csv')
YEAR_CSV_HEADER = ['Objekt', 'Jahr', 'Bereich', 'Zahlungstyp', 'Kategorie', 'Umlagefähigkeit', 'Betrag (EUR)']


def datev_config(ids):
    """A DATEV batch for 2025 of the property without a scope: its bank account, both rent payment types and its property tax."""
    return dict(property_id=str(ids['other_property']), period_start='2025-01-01', period_end='2025-12-31',
                fiscal_year_start='2025-01-01', chart='SKR03', advisor_number='12345', client_number='123', account_length=4,
                bank_accounts={str(ids['b_bank']): '1111'}, payment_accounts={'KALTMIETE': '2222', 'NK_VORAUSZAHLUNG': '2223'},
                category_accounts={str(ids['b_category']): {'account': '3333', 'counter_account': '4444'}},
                gross_accounts_reviewed=True)


async def downloads(api, ids, key='other_property', **scope):
    """What one property offers for download, as (status, body) pairs: the two CSV exports of the annual overview, the
    DATEV sources, and the DATEV preview and file of the year. A refusal's body is its JSON, a download's its bytes (the
    generation time in the first line of the DATEV file left out)."""
    property_id = str(ids[key])
    year = {'property_id': property_id, 'year': 2025, **scope}
    config = {**datev_config(ids), 'property_id': property_id, **scope}
    answers = {
        'property_year_csv': await api.get('/api/reports/property-year.csv', params=year),
        'anlage_v_csv': await api.get('/api/reports/property-year/anlage-v.csv', params=year),
        'datev_sources': await api.get('/api/reports/datev/sources', params={'property_id': property_id, **PERIOD, **scope}),
        'datev_preview': await api.post('/api/reports/datev/preview', json=config),
    }
    digest = answers['datev_preview'].json().get('source_digest', '0' * 64)
    answers['datev_csv'] = await api.post('/api/reports/datev.csv', json={**config, 'expected_source_digest': digest})
    assert answers.keys() == set(DOWNLOADS)

    def body(name):
        answer = answers[name]
        if answer.headers['content-type'].startswith('application/json'):
            return answer.json()
        return answer.content.split(b'\r\n', 1)[1] if name == 'datev_csv' else answer.content
    return {name: (answers[name].status_code, body(name)) for name in DOWNLOADS}


def csv_rows(content):
    return list(csv.reader(io.StringIO(content.decode('utf-8-sig'), newline=''), delimiter=';'))


def downloads_before_the_levy(answers, label):
    """What the property without a scope offers whether or not the organization holds a levy: 600.00 income (500.00
    rent, 100.00 advance), 250.00 property tax and 350.00 balance, from one bank account and three bookings."""
    assert {name: status for name, (status, _) in answers.items()} == dict.fromkeys(DOWNLOADS, 200), answers
    assert csv_rows(answers['property_year_csv'][1]) == [YEAR_CSV_HEADER,
        ['Test', '2025', 'Einnahmen', 'KALTMIETE', '', '', '500,00'],
        ['Test', '2025', 'Einnahmen', 'NK_VORAUSZAHLUNG', '', '', '100,00'],
        ['Test', '2025', 'Ausgaben', '', label, 'UMLAGEFAEHIG', '250,00'],
        ['Test', '2025', 'Einnahmen gesamt', '', '', '', '600,00'],
        ['Test', '2025', 'Ausgaben gesamt', '', '', '', '250,00'],
        ['Test', '2025', 'Saldo', '', '', '', '350,00']]
    anlage_v = csv_rows(answers['anlage_v_csv'][1])
    assert anlage_v[0] == ['Objekt', 'Jahr', 'Bereich', 'Zuordnung', 'Bezeichnung', 'Hinweis', 'Betrag (EUR)']
    # Whatever the Anlage V mapping assigns, it adds up to the figures of the annual overview.
    reconciled = {(row[2], row[4]): row[6] for row in anlage_v if row[3] == 'Abgleich'}
    assert {key: amount for key, amount in reconciled.items() if key[1] == 'Jahresübersicht gesamt'} == {
        ('Einnahmen', 'Jahresübersicht gesamt'): '600,00', ('Werbungskosten', 'Jahresübersicht gesamt'): '250,00'}, anlage_v
    assert {amount for (_, label), amount in reconciled.items() if label == 'Differenz'} == {'0,00'}, anlage_v
    sources = answers['datev_sources'][1]
    assert (sources['payment_total'], sources['cost_total'], sources['payment_types']) == ('600.00', '250.00', ['KALTMIETE', 'NK_VORAUSZAHLUNG'])
    assert [bank['label'] for bank in sources['banks']] == ['Musterhaus']
    preview = answers['datev_preview'][1]
    assert (preview['payment_total'], preview['cost_total']) == ('600.00', '250.00')
    assert sorted((row['amount'], row['side'], row['account'], row['counter_account']) for row in preview['rows']) == [
        ('100.00', 'S', '1111', '2223'), ('250.00', 'S', '3333', '4444'), ('500.00', 'S', '1111', '2222')]
    assert len(csv_rows(answers['datev_csv'][1])) == 4  # the column headings and three postings


async def label_of_the_category(db, ids):
    return await db.scalar(text('SELECT label FROM cost_category WHERE id=:b_category'), ids)


async def test_levy_leaves_the_downloads_of_a_property_without_a_scope_as_before(levy_db, levy_fixture):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_year_books_without_scope(levy_db, ids)
    async with client(ids) as api:
        before = await downloads(api, ids)
        downloads_before_the_levy(before, await label_of_the_category(levy_db, ids))
        await create(api, ids)
        assert await downloads(api, ids) == before


REFUSED_FOR_THE_LEVY_PROPERTY = {'detail': 'Select an exact accounting scope for this property'}


async def grant_export(db, ids, scope):
    """The clerk's grant on a scope also lets the clerk export: the active grant is revoked and replaced by one with `export`."""
    old = await db.scalar(text("SELECT capabilities FROM scope_grant WHERE scope_id=:grant_scope AND user_id=:clerk AND revoked_at IS NULL"), {**ids, 'grant_scope': ids[scope]})
    await db.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:grant_scope AND user_id=:clerk AND revoked_at IS NULL"), {**ids, 'grant_scope': ids[scope]})
    await db.execute(text("INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason) VALUES (:org,:grant_scope,:clerk,:capabilities,:actor,'Export test')"),
                     {**ids, 'grant_scope': ids[scope], 'capabilities': [*old, 'export']})
    await db.commit()


async def test_levy_still_refuses_the_downloads_of_what_the_levy_or_a_scope_concerns(levy_db, levy_fixture):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_year_books_without_scope(levy_db, ids)
    await add_scope_only_property(levy_db, ids)
    await grant_export(levy_db, ids, 'd_scope')
    async with client(ids) as api:
        scoped_before = await downloads(api, ids, 'd_property', accounting_scope_id=str(ids['d_scope']))
        assert {name: status for name, (status, _) in scoped_before.items()}['property_year_csv'] == 200, scoped_before
        await add_levy_only_property(api, levy_db, ids)
        await create(api, ids)
        # The levy's own properties, one of them with the levy as its only classified finance: no download without the scope.
        for key in ('property', 'c_property'):
            answers = await downloads(api, ids, key)
            assert answers == dict.fromkeys(DOWNLOADS, (409, REFUSED_FOR_THE_LEVY_PROPERTY)), (key, answers)
        # A property that has a scope of its own is downloaded through that scope, as before the levy, and not without it.
        assert await downloads(api, ids, 'd_property') == dict.fromkeys(DOWNLOADS, (409, {'detail': SCOPE_REQUIRED}))
        assert await downloads(api, ids, 'd_property', accounting_scope_id=str(ids['d_scope'])) == scoped_before
        # The property without a scope is downloaded.
        assert {status for status, _ in (await downloads(api, ids)).values()} == {200}


async def test_report_scope_source_access_answers_an_export_for_one_property(levy_db, levy_fixture, seed_data):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_scope_only_property(levy_db, ids)
    async with client(ids) as api:
        await add_levy_only_property(api, levy_db, ids)
        assert await property_answer(ids, capability='export', subject=ids['other_property'])
        await create(api, ids)
    export = "SELECT report_scope_source_access(:org,NULL,'export')"
    # The export of the whole organization, which names no property, is refused as it was before this change.
    assert not await read_value(export, ids, actor_key='clerk')
    assert not await property_answer(ids, capability='export')
    # The property without a scope and without classified finance: as before the levy, and an id of no property of the organization too.
    assert await property_answer(ids, capability='export', subject=ids['other_property'])
    assert await property_answer(ids, capability='export', subject=uuid.uuid4())
    assert await property_answer(ids, capability='export', subject=seed_data['org_b_id'])
    # The levy's property, one whose only classified finance is the levy, and one with a scope of its own stay refused.
    for key in ('property', 'c_property', 'd_property'):
        assert not await property_answer(ids, capability='export', subject=ids[key]), key
    # No single subject, or a scope: answered by the three-argument function, whatever property the call names.
    assert not await property_answer(ids, capability='export', subject=ids['other_property'], lease=ids['b_lease'])
    scoped = await read_value("SELECT report_scope_source_access(:org,:scope,'export')", ids, actor_key='clerk')
    assert await property_answer(ids, sphere=ids['scope'], capability='export', subject=ids['other_property']) == scoped
    # A caller of another organization gets no exemption for the levy organization's property.
    engine = create_async_engine(APP_RW_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:user,true)"),
                                     {'org': str(seed_data['org_b_id']), 'user': str(seed_data['user_b_admin_id'])})
            assert not await connection.scalar(text("SELECT report_scope_source_access(:org,NULL,'export',NULL,NULL,:subject)"),
                                               {'org': ids['org'], 'subject': ids['other_property']})
    finally:
        await engine.dispose()


@pytest.mark.parametrize('had_export', [False, True], ids=['never_had_export', 'export_revoked'])
async def test_caller_without_export_access_to_the_levy_scope_downloads_the_property_without_a_scope_without_levy_data(levy_db, levy_fixture, had_export):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_year_books_without_scope(levy_db, ids)
    if had_export:
        await grant_export(levy_db, ids, 'scope')
    async with client(ids) as api:
        before = await downloads(api, ids)
        downloads_before_the_levy(before, await label_of_the_category(levy_db, ids))
        levy = await create(api, ids)
    if had_export:
        # The clerk loses every grant on the levy's scope, the export grant included.
        await levy_db.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL'), ids)
        await levy_db.commit()
    async with client(ids) as caller:
        answers = await downloads(caller, ids)
        assert answers == before
        for _, body in answers.values():
            assert levy['id'] not in str(body) and '3000.00' not in str(body) and '1200.00' not in str(body)
        # Nothing of the levy's property, nor of its scope, is offered to the caller.
        assert await downloads(caller, ids, 'property') == dict.fromkeys(DOWNLOADS, (409, REFUSED_FOR_THE_LEVY_PROPERTY))
        scoped = await downloads(caller, ids, 'property', accounting_scope_id=str(ids['scope']))
        assert scoped == dict.fromkeys(DOWNLOADS, (404, {'detail': 'Accounting scope not accessible'}))


async def test_foreign_organization_downloads_nothing_of_the_property_without_a_scope(levy_db, levy_fixture, client_b_admin):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_year_books_without_scope(levy_db, ids)
    async with client(ids) as api:
        await create(api, ids)
    answers = await downloads(client_b_admin, ids)
    assert answers == dict.fromkeys(DOWNLOADS, (404, {'detail': 'Property not found'}))
    scoped = await downloads(client_b_admin, ids, 'property', accounting_scope_id=str(ids['scope']))
    assert scoped == dict.fromkeys(DOWNLOADS, (404, {'detail': 'Property not found'}))


async def test_caller_who_may_read_but_not_export_is_answered_as_before_the_levy(levy_db, levy_fixture):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_year_books_without_scope(levy_db, ids)
    await add_scope_only_property(levy_db, ids)
    scoped = {'accounting_scope_id': str(ids['d_scope'])}
    async with client(ids) as api:
        # The clerk reads the property through its scope, where the grant has `read` and no `export`.
        assert (await api.get('/api/reports/property-year', params={'property_id': str(ids['d_property']), 'year': 2025, **scoped})).status_code == 200
        before = await downloads(api, ids, 'd_property', **scoped)
        assert {status for status, _ in before.values()} == {404}, before
        await create(api, ids)
        assert await downloads(api, ids, 'd_property', **scoped) == before
        # A property without a scope is still downloaded, which the clerk's export right on no scope does not change.
        assert {status for status, _ in (await downloads(api, ids)).values()} == {200}


async def test_enforced_sources_refuse_the_downloads_of_the_property_without_a_scope_as_before_the_levy(levy_db, levy_fixture, scope_seed):
    ids = levy_fixture
    await add_property_without_scope(levy_db, ids)
    await add_year_books_without_scope(levy_db, ids)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        before = await downloads(api, ids)
    async with client(ids) as api:
        await create(api, ids)
    async with synthetic_enforcement(scope_seed), client(ids) as api:
        # An enforced organization hides and refuses everything of a property by itself, so the levy adds nothing to its answers.
        assert await downloads(api, ids) == before
        assert {status for status, _ in before.values()} == {404}, before
