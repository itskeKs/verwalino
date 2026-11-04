"""Synthetic households and costs through the ordinary statement API."""
from tests.settlement_period_support import create, period_property, statement

ANNA_HOUSEHOLD = [
    {'valid_from': '2026-01-01', 'persons': 2, 'is_vacancy': False},
    {'valid_from': '2026-06-01', 'persons': 3, 'is_vacancy': False},
]
BERND_HOUSEHOLD = [
    {'valid_from': '2026-01-01', 'persons': 1, 'is_vacancy': True},
    {'valid_from': '2026-03-01', 'persons': 1, 'is_vacancy': False},
]


async def persons_property(api, *, save_households=True):
    data = await period_property(api, month=1)
    second = await create(api, '/api/units', {'property_id': data['property']['id'], 'number': '2', 'usage_type': 'WOHNUNG', 'area': '50'})
    bernd = await create(api, '/api/leases', {'unit_id': second['id'], 'tenant_profile_id': data['tenant']['id'],
        'valid_from': '2026-03-01', 'base_rent': '700', 'advance_payment': '0'})
    await create(api, '/api/ownership', {'unit_id': second['id'], 'owner_profile_id': data['owner']['id'], 'valid_from': '2020-01-01'})
    category = next(c for c in (await api.get('/api/cost-categories')).json() if c['code'] == 'GARTENPFLEGE')
    cost = await create(api, '/api/cost-entries', {'property_id': data['property']['id'], 'cost_category_id': category['id'],
        'invoice_date': '2026-12-31', 'service_period_from': '2026-01-01', 'service_period_to': '2026-12-31',
        'gross_amount': '1250.00', 'tenant_distribution_key': 'PERSONENZAHL', 'tenant_apportionable': 'UMLAGEFAEHIG'})
    response = await api.post(f"/api/cost-entries/{cost['id']}/confirm")
    assert response.status_code == 200, response.text
    data.update(second=second, bernd=bernd, cost=cost)
    if save_households:
        for unit, rows in [(data['unit'], ANNA_HOUSEHOLD), (second, BERND_HOUSEHOLD)]:
            response = await api.put(f"/api/units/{unit['id']}/household", json=rows)
            assert response.status_code == 200, response.text
    return data


async def persons_run(api, data, *, bernd=False):
    payload = statement(data, '2026-01-01', '2026-12-31')
    if bernd:
        payload['lease_agreement_id'] = data['bernd']['id']
    run = await create(api, '/api/settlements', {**payload,
        'advance_payments_total': '0.00', 'heating_advance_payments_total': '0.00'})
    from tests.financial_coverage_helpers import attest_fixture
    await attest_fixture(api, 'settlement', run['id'], {
        'advance_payments_total': '0.00', 'advance_payments_source': 'MANUAL',
        'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL'})
    return run
