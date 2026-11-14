"""Personal design checks through the real authenticated API."""
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.second_factor import finish_sign_in


@then(parsers.parse('the personal design is "{theme}"'))
def personal_design_is(world, theme):
    answer = world.act('GET', '/api/auth/preferences')
    assert answer.status_code == 200, world.describe_response()
    assert answer.json() == {'design_theme': theme}


@when(parsers.parse('the user chooses the personal design "{theme}"'))
def choose_personal_design(world, theme):
    answer = world.act('PATCH', '/api/auth/preferences', json={'design_theme': theme})
    assert answer.status_code == 200, world.describe_response()


@when('the user signs out and signs in again with the current password')
def sign_in_again(world):
    assert world.act('POST', '/api/auth/logout').status_code == 204
    answer = world.act('POST', '/api/auth/login', json={'email': world.data['email'], 'password': 'testpass1234'})
    answer = finish_sign_in(world, world.actor, answer)
    assert answer.status_code == 200, world.describe_response()


@given('the organizations have independent personal designs')
def independent_designs(world, request):
    clients = list(world.organizations.values())
    before = [(client, world.look(client, '/api/auth/preferences').json()) for client in clients]
    assert all('design_theme' in preferences for _, preferences in before)
    world.data['design_preferences_before'] = before
    def restore():
        for client, preferences in before:
            world.run(client.patch('/api/auth/preferences', json=preferences))
    request.addfinalizer(restore)


@when('Verwaltung Alpha changes its personal design')
def alpha_changes_design(world):
    client = world.administrator_of('Verwaltung Alpha')
    assert world.send(client, 'PATCH', '/api/auth/preferences', json={'design_theme': 'warm'}).status_code == 200


@then('Verwaltung Beta keeps its personal design')
def beta_keeps_design(world):
    client = world.administrator_of('Verwaltung Beta')
    expected = next(value for owner, value in world.data['design_preferences_before'] if owner is client)
    answer = world.look(client, '/api/auth/preferences')
    assert answer.status_code == 200 and answer.json() == expected
