"""An organization with a special levy answers unscoped reads like classified data does (#722, #739)."""
import importlib
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


async def test_the_new_overload_has_the_attributes_and_acl_of_the_function_it_extends(levy_db):
    old, new = 'report_scope_source_access(uuid,uuid,text)', 'report_scope_source_access(uuid,uuid,text,uuid,uuid)'
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
    functions = text("""SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid),p.proacl::text,p.proconfig,p.proowner
        FROM pg_proc p WHERE p.pronamespace='public'::regnamespace ORDER BY 1""")
    new = 'report_scope_source_access(uuid,uuid,text,uuid,uuid)'
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
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
