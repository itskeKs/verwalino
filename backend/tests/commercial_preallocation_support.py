"""Synthetic mixed-property inputs through the ordinary HTTP workflow."""
from tests.settlement_period_support import create
from tests.financial_coverage_helpers import attest_fixture

REASON = 'Gastronomie, Restmüll 2 × 1.100 l'


async def commercial_property(api):
    prop = await create(api, '/api/properties', {'name': 'Gewerbehof', 'management_type': 'MIET'})
    rows = []
    for number, usage, area in [('Laden', 'GEWERBE', '60'), ('Wohnung 1', 'WOHNUNG', '75'), ('Wohnung 2', 'WOHNUNG', '65')]:
        unit = await create(api, '/api/units', {'property_id': prop['id'], 'number': number, 'usage_type': usage, 'area': area})
        person = await create(api, '/api/persons', {'last_name': number})
        tenant = await create(api, '/api/tenant-profiles', {'person_id': person['id']})
        lease = await create(api, '/api/leases', {'unit_id': unit['id'], 'tenant_profile_id': tenant['id'],
            'valid_from': '2020-01-01', 'base_rent': '700.00', 'advance_payment': '0.00', 'heating_advance_payment': '0.00'})
        rows.append({'unit': unit, 'lease': lease})
    category = next(row for row in (await api.get('/api/cost-categories')).json() if row['code'] == 'MUELLBESEITIGUNG')
    cost = await create(api, '/api/cost-entries', {'property_id': prop['id'], 'cost_category_id': category['id'],
        'invoice_date': '2026-12-31', 'service_period_from': '2026-01-01', 'service_period_to': '2026-12-31',
        'gross_amount': '1200.00', 'tenant_apportionable': 'UMLAGEFAEHIG', 'tenant_distribution_key': 'WOHNFLAECHE'})
    response = await api.post(f"/api/cost-entries/{cost['id']}/confirm")
    assert response.status_code == 200, response.text
    for row in rows:
        row['run'] = await create(api, '/api/settlements', {'property_id': prop['id'], 'lease_agreement_id': row['lease']['id'],
            'period_start': '2026-01-01', 'period_end': '2026-12-31',
            'advance_payments_total': '0.00', 'heating_advance_payments_total': '0.00'})
        await attest_fixture(api, 'settlement', row['run']['id'], {'advance_payments_total': '0.00',
            'advance_payments_source': 'MANUAL', 'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL'})
    return {'property': prop, 'cost': cost, 'rows': rows}


def commercial_rule(data, **changes):
    return {'cost_entry_id': data['cost']['id'], 'commercial_share_percent': '50',
        'commercial_amount': None, 'commercial_reason': REASON, **changes}
