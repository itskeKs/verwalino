"""T52: monthly assigned rent, historical arrears and explicit vacancy date."""
import asyncio
import uuid

import pytest
import pytest_asyncio

from tests.test_deposits import pay
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401

from tests.test_rent_changes import rent_db, rent_world, api_client, post_change  # noqa: F401

ROOT = '/api/reports/dashboard-kpis'


@pytest.mark.asyncio
async def test_dashboard_uses_effective_heating_while_deadlines_keep_original_contract(rent_world):
    w = rent_world
    async with api_client(w) as api:
        response = await api.post(f"/api/leases/{w['lease']}/rent-changes", json={
            'valid_from': '2026-10-01', 'base_rent': '750.00', 'advance_payment': '150.00',
            'heating_advance_payment': '90.00'})
        assert response.status_code == 201, response.text
        response = await api.get(ROOT, params=query(w))
        assert response.status_code == 200, response.text
        assert response.json()['expected_rent'] == '990.00'
        response = await api.get('/api/fristen', params={'reference_date': '2026-10-31'})
        assert response.status_code == 200, response.text
        overdue = next(row for row in response.json() if row['kind'] == 'OFFENE_POSTEN')
        # Deadlines retain the original contract: 20 rent/BK items per lease.
        # The heating-only history change is included by the dashboard above.
        assert overdue['detail'].startswith('40 '), overdue


def query(world, **values):
    return {'property_id': str(world['property']), 'year': 2026, 'month': 10, **values}


@pytest.mark.asyncio
async def test_dashboard_kpis_exact_month_and_cumulative_unpaid_rent(rent_world):
    async with api_client(rent_world) as api:
        before = (await api.get('/api/leases', params={'unit_id': str(rent_world['unit'])})).json()
        response = await api.get(ROOT, params=query(rent_world))
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['property_id'] == str(rent_world['property'])
        assert (data['period_start'], data['period_end'], data['occupancy_date']) == ('2026-10-01', '2026-10-31', '2026-10-31')
        assert data['payment_basis'] == 'assigned_rent_period'
        assert (data['expected_rent'], data['received_rent'], data['arrears']) == ('850.00', '0.00', '8500.00')
        assert data['arrears_count'] == 1
        assert (data['unit_count'], data['occupied_units'], data['vacant_units']) == (1, 1, 0)
        assert (await api.get('/api/leases', params={'unit_id': str(rent_world['unit'])})).json() == before


@pytest.mark.asyncio
async def test_dashboard_kpis_effective_rent_history_and_partial_month(rent_world):
    async with api_client(rent_world) as api:
        await post_change(api, rent_world, valid_from='2026-10-16', base_rent='760.00', advance_payment='150.00')
        response = await api.get(ROOT, params=query(rent_world))
        assert response.status_code == 200, response.text
        data = response.json()
        assert (data['expected_rent'], data['arrears']) == ('880.97', '8530.97')
        end = await api.patch(f"/api/leases/{rent_world['lease']}", json={'valid_to': '2026-10-16'})
        assert end.status_code == 200, end.text
        response = await api.get(ROOT, params=query(rent_world))
        assert response.status_code == 200, response.text
        data = response.json()
        # 15 days at 700 + one at 760, then 16/31 NK; independently fixed cents.
        assert (data['expected_rent'], data['arrears']) == ('440.65', '8090.65')
        assert (data['occupied_units'], data['vacant_units']) == (0, 1)


@pytest.mark.asyncio
async def test_dashboard_kpis_read_only_role_and_parallel_reads(rent_world):
    async with api_client(rent_world, 'reader') as api:
        responses = await asyncio.gather(*[api.get(ROOT, params=query(rent_world)) for _ in range(2)])
        assert [r.status_code for r in responses] == [200, 200]
        assert responses[0].json() == responses[1].json()


@pytest.mark.asyncio
async def test_dashboard_kpis_foreign_and_unknown_property_do_not_disclose(rent_world):
    async with api_client(rent_world) as api:
        for property_id in (rent_world['foreign_property'], uuid.uuid4()):
            response = await api.get(ROOT, params=query(rent_world, property_id=str(property_id)))
            assert response.status_code == 404, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('values', [{'month': 0}, {'month': 13}, {'year': 0}, {'year': 10000}])
async def test_dashboard_kpis_invalid_month_and_year(rent_world, values):
    async with api_client(rent_world) as api:
        response = await api.get(ROOT, params=query(rent_world, **values))
        assert response.status_code == 422, response.text


@pytest_asyncio.fixture
async def kpi_world(rent_world):
    async with api_client(rent_world) as api:
        response = await api.post('/api/bank-accounts', json={
            'property_id': str(rent_world['property']), 'account_type': 'LAUFEND', 'label': 'T52 rent'})
        assert response.status_code == 201, response.text
        rent_world['bank'] = response.json()['id']
    return rent_world


async def rent_payment(api, world, **values):
    return await pay(api, world, amount='700.00', payment_type='KALTMIETE',
                     period_year=2026, period_month=10, **values)


@pytest.mark.asyncio
async def test_dashboard_kpis_assigned_month_not_value_date_and_no_financial_mutation(kpi_world):
    w = kpi_world
    async with api_client(w) as api:
        await rent_payment(api, w, value_date='2027-01-05')
        await pay(api, w, amount='150.00', payment_type='NK_VORAUSZAHLUNG', period_year=2026,
                  period_month=10, value_date='2025-12-31')
        await pay(api, w, amount='50.00', payment_type='KALTMIETE', direction='OUT',
                  period_year=2026, period_month=10)
        # Future assigned rent and security deposits cannot inflate October rent.
        await pay(api, w, amount='999.00', payment_type='KALTMIETE', period_year=2026, period_month=11)
        await pay(api, w, amount='999.00', payment_type='KAUTION')
        before = (await api.get('/api/payments')).json()
        history = (await api.get(f"/api/leases/{w['lease']}/rent-changes")).json()
        response = await api.get(ROOT, params=query(w))
        assert response.status_code == 200, response.text
        data = response.json()
        assert (data['expected_rent'], data['received_rent'], data['arrears']) == ('850.00', '800.00', '7700.00')
        assert data['unassigned_rent_count'] == 0
        assert (await api.get('/api/payments')).json() == before
        assert (await api.get(f"/api/leases/{w['lease']}/rent-changes")).json() == history


@pytest.mark.asyncio
async def test_dashboard_kpis_ignores_drafts_and_both_storno_rows(kpi_world):
    w = kpi_world
    async with api_client(w) as api:
        await rent_payment(api, w, book=False)
        row = await rent_payment(api, w)
        reversed_payment = await api.post(f"/api/payments/{row['id']}/reverse", json={'note': 'T52 correction'})
        assert reversed_payment.status_code == 201, reversed_payment.text
        response = await api.get(ROOT, params=query(w))
        assert response.status_code == 200, response.text
        assert (response.json()['received_rent'], response.json()['arrears']) == ('0.00', '8500.00')


@pytest.mark.asyncio
async def test_dashboard_kpis_unassigned_rent_is_warning_not_value_date_fallback(kpi_world):
    w = kpi_world
    async with api_client(w) as api:
        await pay(api, w, amount='123.45', payment_type='KALTMIETE', value_date='2026-10-01')
        response = await api.get(ROOT, params=query(w))
        assert response.status_code == 200, response.text
        data = response.json()
        assert (data['received_rent'], data['arrears']) == ('0.00', '8500.00')
        assert (data['unassigned_rent_count'], data['unassigned_rent_amount']) == (1, '123.45')


@pytest.mark.asyncio
async def test_dashboard_kpis_never_offsets_another_lease_with_credit(kpi_world):
    w = kpi_world
    async with api_client(w) as api:
        unit = await api.post('/api/units', json={'property_id': str(w['property']), 'number': '2', 'usage_type': 'WOHNUNG'})
        assert unit.status_code == 201, unit.text
        person = await api.post('/api/persons', json={'first_name': 'T52', 'last_name': 'Credit'})
        assert person.status_code == 201, person.text
        tenant = await api.post('/api/tenant-profiles', json={'person_id': person.json()['id']})
        assert tenant.status_code == 201, tenant.text
        lease = await api.post('/api/leases', json={'unit_id': unit.json()['id'], 'tenant_profile_id': tenant.json()['id'],
            'valid_from': '2026-10-01', 'base_rent': '100.00', 'advance_payment': '0.00'})
        assert lease.status_code == 201, lease.text
        await pay(api, {**w, 'tenant': tenant.json()['id'], 'lease': lease.json()['id']},
                  amount='20000.00', payment_type='KALTMIETE', period_year=2026, period_month=10)
        response = await api.get(ROOT, params=query(w))
        assert response.status_code == 200, response.text
        data = response.json()
        assert (data['expected_rent'], data['received_rent'], data['arrears'], data['arrears_count']) == ('950.00', '20000.00', '8500.00', 1)
        assert (data['unit_count'], data['occupied_units'], data['vacant_units']) == (2, 2, 0)


async def scoped_kpi_world(session, seed, **options):
    from sqlalchemy import text
    from tests.test_property_year_report import fixture, ready
    ids = await fixture(session, seed, management_type=options.pop('management_type', 'MIET'), **options)
    ids['lease'] = uuid.uuid4()
    if options.get('whole', True):
        ids['unit'] = uuid.uuid4()
        await session.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:unit,:org,:property,'KPI','WOHNUNG')"), ids)
    await session.execute(text("""INSERT INTO lease_agreement(id,organization_id,unit_id,tenant_profile_id,valid_from,base_rent,advance_payment)
        VALUES (:lease,:org,:unit,:tenant,'2026-01-01',700,150)"""), ids)
    await ready(session, ids)
    return ids


@pytest.mark.asyncio
async def test_dashboard_kpis_bound_scope_read_and_live_revocation(migrator_session, scope_seed):
    from tests.test_property_year_report import client
    from tests.test_scoped_account_statements import mutate
    from sqlalchemy import text
    ids = await scoped_kpi_world(migrator_session, scope_seed)
    params = {'property_id': str(ids['property']), 'year': 2026, 'month': 10}
    async with client(ids) as api:
        assert (await api.get(ROOT, params=params)).status_code == 409
        params['accounting_scope_id'] = str(ids['scope'])
        response = await api.get(ROOT, params=params)
        assert response.status_code == 200, response.text
        assert (response.json()['expected_rent'], response.json()['arrears']) == ('850.00', '8500.00')
        await mutate(text("UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL"), ids)
        assert (await api.get(ROOT, params=params)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [
    {'whole': False}, {'scope_type': 'WEG'}, {'start': '2026-10-01'}, {'end': '2026-10-15'},
])
async def test_dashboard_kpis_rejects_partial_wrong_or_historically_incomplete_scope(migrator_session, scope_seed, options):
    from tests.test_property_year_report import client
    ids = await scoped_kpi_world(migrator_session, scope_seed, **options)
    async with client(ids) as api:
        response = await api.get(ROOT, params={'property_id': str(ids['property']), 'year': 2026,
            'month': 10, 'accounting_scope_id': str(ids['scope'])})
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_dashboard_kpis_mixed_property_with_complete_miet_scope_is_supported(migrator_session, scope_seed):
    from tests.test_property_year_report import client
    ids = await scoped_kpi_world(migrator_session, scope_seed, management_type='MISCH')
    async with client(ids) as api:
        response = await api.get(ROOT, params={'property_id': str(ids['property']), 'year': 2026,
            'month': 10, 'accounting_scope_id': str(ids['scope'])})
        assert response.status_code == 200, response.text
        assert (response.json()['expected_rent'], response.json()['arrears']) == ('850.00', '8500.00')


@pytest.mark.asyncio
async def test_dashboard_kpis_before_lease_start_shows_zero_and_explicit_vacancy(rent_world):
    async with api_client(rent_world) as api:
        response = await api.get(ROOT, params=query(rent_world, year=2025, month=12))
        assert response.status_code == 200, response.text
        data = response.json()
        assert (data['expected_rent'], data['received_rent'], data['arrears']) == ('0.00', '0.00', '0.00')
        assert (data['unit_count'], data['occupied_units'], data['vacant_units']) == (1, 0, 1)
        assert data['occupancy_date'] == '2025-12-31'


@pytest.mark.asyncio
async def test_dashboard_kpis_orphan_rent_is_rejected_instead_of_omitted(kpi_world):
    w = kpi_world
    async with api_client(w) as api:
        await pay(api, w, amount='10.00', payment_type='KALTMIETE', lease_agreement_id=None,
                  period_year=2026, period_month=10)
        response = await api.get(ROOT, params=query(w))
        assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_dashboard_kpis_low_outer_decimal_precision_does_not_round_money(kpi_world):
    from decimal import localcontext
    w = kpi_world
    async with api_client(w) as api:
        await pay(api, w, amount='1234567.89', payment_type='KALTMIETE', period_year=2026, period_month=10)
        with localcontext() as arithmetic:
            arithmetic.prec = 6
            response = await api.get(ROOT, params=query(w))
        assert response.status_code == 200, response.text
        assert (response.json()['received_rent'], response.json()['arrears']) == ('1234567.89', '0.00')


@pytest.mark.asyncio
async def test_dashboard_kpis_waits_for_existing_financial_input_lock(rent_world, rent_db):
    from sqlalchemy import text
    await rent_db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                          {'key': f"financial-inputs:{rent_world['org']}"})
    blocker = await rent_db.scalar(text('SELECT pg_backend_pid()'))
    async with api_client(rent_world) as api:
        request = asyncio.create_task(api.get(ROOT, params=query(rent_world)))
        waiting = False
        try:
            for _ in range(200):
                waiting = await rent_db.scalar(text("""SELECT EXISTS(SELECT 1 FROM pg_locks l
                    WHERE l.locktype='advisory' AND NOT l.granted
                    AND :blocker=ANY(pg_blocking_pids(l.pid)))"""), {'blocker': blocker})
                if waiting or request.done():
                    break
                await asyncio.sleep(0.01)
        finally:
            await rent_db.rollback()
        response = await asyncio.wait_for(request, timeout=15)
        assert waiting, 'KPI report must wait for the existing financial-input boundary'
        assert response.status_code == 200, response.text
