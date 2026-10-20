"""T29 meters inherit existing property-master read/prepare authority."""
import pytest

from tests.test_property_master_access import (
    scope_seed, worker_tenant, committed_actor,  # noqa: F401
    existing, principals,
)
from tests.test_scoped_account_statements import client
from tests.test_property_source_access import grant
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode

pytestmark = pytest.mark.asyncio


async def meter(api, prop, unit):
    response = await api.post('/api/meters', json={
        'property_id': prop, 'unit_id': unit, 'meter_number': 'T29 private meter',
        'meter_type': 'KALTWASSER', 'location': 'Protected fixture',
    })
    assert response.status_code == 201, response.text
    return response.json()


async def test_meter_read_is_not_prepare_and_revocation_hides_every_route(scope_seed):
    async with client(principals(scope_seed), 'actor') as api:
        prop, unit = await existing(api)
        created = await meter(api, prop, unit)
        path = f"/api/meters/{created['id']}"
        response = await api.post(path + '/readings', json={'reading_date': '2026-01-01', 'value': '123.4567'})
        assert response.status_code == 201, response.text
        domain, receipt = await grant(api, scope_seed, prop, caps=['read'])
        async with synthetic_enforcement(scope_seed):
            assert (await api.get(path)).status_code == 200
            assert [row['id'] for row in (await api.get('/api/meters')).json()] == [created['id']]
            assert (await api.get(path + '/readings')).json()[0]['value'] == '123.4567'
            assert (await api.patch(path, json={'location': 'No prepare'})).status_code == 404
            assert (await api.post(path + '/readings', json={'reading_date': '2026-12-31', 'value': '140'})).status_code == 404
            assert (await api.post('/api/meters', json={'property_id': prop, 'meter_number': 'Denied', 'meter_type': 'KALTWASSER'})).status_code == 404
            revoked = await api.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')
            assert revoked.status_code == 204, revoked.text
            assert (await api.get('/api/meters')).json() == []
            for target in (path, path + '/readings'):
                response = await api.get(target)
                assert response.status_code == 404, response.text
            assert (await api.get('/api/meters', params={'property_id': prop})).status_code == 404
            assert (await api.get('/api/meters', params={'unit_id': unit})).status_code == 404


async def test_meter_prepare_respects_paused_master_data(scope_seed):
    async with client(principals(scope_seed), 'actor') as api:
        prop, unit = await existing(api)
        await grant(api, scope_seed, prop, caps=['read', 'prepare'])
        async with synthetic_enforcement(scope_seed):
            created = await meter(api, prop, unit)
            path = f"/api/meters/{created['id']}"
            assert (await api.patch(path, json={'location': 'Permitted'})).status_code == 200
            response = await api.post(path + '/readings', json={'reading_date': '2026-01-01', 'value': '0.0001'})
            assert response.status_code == 201, response.text
            await change_mode(scope_seed, 'PAUSED')
            assert (await api.get(path)).status_code == 200
            assert (await api.get(path + '/readings')).json()[0]['value'] == '0.0001'
            assert (await api.patch(path, json={'location': 'Paused'})).status_code == 404
            assert (await api.post(path + '/readings', json={'reading_date': '2026-12-31', 'value': '10'})).status_code == 404
