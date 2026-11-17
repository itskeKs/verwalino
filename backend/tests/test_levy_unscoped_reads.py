"""An organization with a special levy answers unscoped overview reads like classified data does (#722)."""
import uuid

import pytest
from sqlalchemy import text

from tests.test_financial_scope_boundary import scope_seed as scope_seed
from tests.test_invoice_extraction_worker import worker_tenant as worker_tenant
from tests.test_property_source_enforcement import synthetic_enforcement
from tests.test_special_levy import client, create, levy_db as levy_db, levy_fixture as levy_fixture, query

pytestmark = pytest.mark.asyncio

PERIOD = {'period_start': '2025-01-01', 'period_end': '2025-12-31'}
SCOPE_REQUIRED = ('Wählen Sie einen eindeutigen Abrechnungskreis und prüfen Sie noch nicht zugeordnete Daten, '
                  'bevor Sie die Zahlungen berechnen.')
MONITOR_SCOPE_REQUIRED = ('The organization-wide deadline monitor is unavailable for classified financial data; '
                          'a scoped view is required')
READS = ('context', 'fristen', 'open_items', 'mietkonto_owner', 'mietkonto_owner_plan', 'dunning_owner', 'dunning_tenant')
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
    """Every unscoped overview read the issue names, as (status, body) pairs."""
    owner = str(ids['owners'][0])
    answers = {
        'context': await api.get('/api/fristen/context'),
        'fristen': await api.get('/api/fristen', params={'reference_date': '2025-12-31'}),
        'open_items': await api.get('/api/payments/open-items', params=PERIOD),
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
