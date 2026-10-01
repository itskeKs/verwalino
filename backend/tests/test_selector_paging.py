"""Bounded keyset selectors retain duplicate names and reject cursor misuse."""
import uuid
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_property_pages_preserve_equal_names_and_scope(client_a_admin, client_a_clerk, client_b_admin, migrator_session, seed_data):
    prefix = f"Paging-{uuid.uuid4()}"
    expected = []
    for i in range(55):
        ident = uuid.uuid4()
        expected.append(ident)
        await migrator_session.execute(text("INSERT INTO property(id,organization_id,name,street,zip_code,city,management_type) VALUES (:id,:org,:name,'','','','MIET')"), {"id": ident, "org": seed_data["org_a_id"], "name": prefix})
    await migrator_session.commit()
    result = await client_a_admin.get('/api/properties/page', params={"q": prefix})
    assert result.status_code == 200, result.text
    first = result.json()
    assert len(first['items']) == 50 and first['next_cursor']
    result = await client_a_admin.get('/api/properties/page', params={"q": prefix, "cursor": first['next_cursor']})
    assert result.status_code == 200, result.text
    second = result.json()
    assert len(second['items']) == 5 and second['next_cursor'] is None
    assert [item['id'] for item in first['items']+second['items']] == sorted(map(str, expected))
    for client, params in (
        (client_b_admin, {"q": prefix, "cursor": first['next_cursor']}),
        (client_a_clerk, {"q": prefix, "cursor": first['next_cursor']}),
        (client_a_admin, {"q": "changed", "cursor": first['next_cursor']}),
        (client_a_admin, {"q": prefix, "cursor": first['next_cursor']+'x'}),
        (client_a_admin, {"q": prefix, "limit": 201}),
    ):
        assert (await client.get('/api/properties/page', params=params)).status_code == 422
    assert (await client_b_admin.get('/api/properties/page', params={"q": prefix})).json()['items'] == []


@pytest.mark.parametrize('path', ['persons', 'users', 'units'])
async def test_selector_contract_and_invalid_cursor(client_a_admin, path):
    result = await client_a_admin.get(f'/api/{path}/page', params={"limit": 1})
    assert result.status_code == 200, result.text
    assert len(result.json()['items']) <= 1
    assert 'next_cursor' in result.json()
    assert (await client_a_admin.get(f'/api/{path}/page', params={"cursor": "invalid"})).status_code == 422
