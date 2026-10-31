"""Diagnostics downloads expose technical metadata without organization records."""
import json

from pytest_bdd import then, when

from tests.test_diagnostics import unpack_bundle


@when('the administrator downloads a diagnostics bundle')
def download_diagnostics(world):
    response = world.act('POST', '/api/admin/diagnostics')
    assert response.status_code == 200, world.describe_response()
    world.data['diagnostics'] = unpack_bundle(response.content)


@then('the bundle contains technical counts without either organization’s records')
def diagnostics_have_no_records(world):
    members = world.data['diagnostics']
    assert json.loads(members['database.json'])['scope'] == 'organization'
    assert json.loads(members['containers.json']) is None
    for organization, client in world.organizations.items():
        assert all(organization not in content for content in members.values())
        people = world.run(client.get('/api/persons')).json()
        for person in people:
            assert all(person['id'] not in content for content in members.values())
