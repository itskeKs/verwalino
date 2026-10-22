"""Read-only statements select one live accounting sphere before loading money."""
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.conftest import MIGRATOR_URL

from app.core.config import settings
from tests.conftest import _make_session_cookie as create_session_token
from app.main import app
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
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
        assert response.json()['ist_total'] == '100.00'
        assert response.json()['soll_total'] == ('7200.00' if kind == 'lease' else '0.00')
        legacy = {k: v for k, v in query(ids).items() if k != 'accounting_scope_id'}
        assert (await api.get(url(ids, kind), params=legacy)).status_code == 409
        if kind == 'lease':
            items = await api.get(f"/api/payments/open-items/lease/{ids['lease']}", params=query(ids))
            assert items.status_code == 200, items.text
            assert sum(float(row['ist']) for row in items.json()) == 100
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
