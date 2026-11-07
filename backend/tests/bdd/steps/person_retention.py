"""Person privacy operations use the real HTTP endpoints."""
import os

import httpx
import pytest
from pytest_bdd import given, when, then

from tests.bdd.steps.browser_setup import create
from tests.bdd.world import target_url
from tests.person_retention_support import repayment_recipient, manual_cost_recipient


def retention_setup(world, request, seed_data, *, isolated=None):
    """Keep local fixtures local and each organization-wide privacy check isolated."""
    if not target_url():
        source = request.getfixturevalue('rent_world')
        client = request.getfixturevalue('export_client')
        world.actor = client
        yield {**source, 'client': client}
        return

    client = world.administrator_of('Verwaltung Alpha')
    source = {'org': seed_data['org_a_id'], 'admin': seed_data['user_a_admin_id']}
    if isolated:
        prefix = {'supplier': 'VERWALINO_TARGET_RETENTION',
            'anna': 'VERWALINO_TARGET_RETENTION_ANNA', 'eva': 'VERWALINO_TARGET_RETENTION_EVA'}[isolated]
        email = os.environ.get(prefix + '_EMAIL')
        password = os.environ.get(prefix + '_PASSWORD')
        assert email and password, f'Retention fixture requires disposable {prefix}_EMAIL and {prefix}_PASSWORD'
        client = httpx.AsyncClient(base_url=target_url(), timeout=60)
    try:
        if isolated:
            response = world.send(client, 'POST', '/api/auth/login', json={'email': email, 'password': password})
            assert response.status_code == 200, 'Disposable retention admin login failed'
            identity = response.json()
            source = {'org': identity['organization_id'], 'admin': identity['user_id']}
            assert source['org'] != seed_data['org_a_id'], 'Retention fixture requires a separate organization'
            response = world.send(client, 'GET', '/api/properties')
            assert response.status_code == 200 and response.json() == [], 'Retention fixture requires an empty organization'
        world.actor = client
        home = create(world, '/api/properties', {'name': 'Aufbewahrungsprüfung', 'management_type': 'MIET',
            'data_audience': {'label': 'Aufbewahrungsprüfung', 'reason': 'Synthetic person retention',
                'grants': [{'user_id': str(source['admin']), 'capabilities': ['read', 'prepare', 'approve', 'export'],
                    'reason': 'Synthetic person retention'}]}}, keyed=True)
        yield {**source, 'client': client, 'property': home['id']}
    finally:
        if isolated:
            world.run(client.aclose())


@pytest.fixture
def retention_context(world, request, seed_data):
    yield from retention_setup(world, request, seed_data)


@pytest.fixture
def supplier_retention_context(world, request, seed_data):
    yield from retention_setup(world, request, seed_data, isolated='supplier')


@pytest.fixture
def reserve_retention_context(world, request, seed_data):
    identity = {
        'test_a_retained_repayment_recipient_remains_identifiable_after_a_deletion_request': 'anna',
        'test_erasing_an_unbound_incidental_contact_cleans_only_the_reserve_reason_copies': 'eva',
    }[request.node.name]
    yield from retention_setup(world, request, seed_data, isolated=identity)


@given('an otherwise inactive supplier has a confirmed 2020 invoice for services in 2017')
def later_invoice(world, supplier_retention_context):
    source = supplier_retention_context
    person, cost = world.run(manual_cost_recipient(source['client'], source['property']))
    world.data.update(retention_person=person, retention_cost=cost)


@then('individual and organization previews retain the supplier through 2028')
def same_supplier_deadline(world):
    preview = world.act('POST', '/api/organizations/deletion')
    assert preview.status_code == 200, world.describe_response()
    assert preview.json()['retention']['retained']['person']['until'] == '2028-12-31'
    status = world.data['person_retention_status']
    assert status['governing_retention_until'] == '2028-12-31'
    assert status['decision'] == 'RESTRICT'


@then('the supplier is restricted and the confirmed invoice is unchanged')
def restricted_supplier(world):
    assert world.data['person_erasure']['applied'] == 'RESTRICTED'
    person, cost = world.data['retention_person'], world.data['retention_cost']
    answer = world.act('GET', f"/api/dsgvo/persons/{person['id']}/export")
    assert answer.status_code == 200, world.describe_response()
    assert answer.json()['person']['last_name'] == person['last_name']
    answer = world.act('GET', f"/api/cost-entries/{cost['id']}")
    assert answer.status_code == 200, world.describe_response()
    assert answer.json() == cost


@given('an otherwise inactive person has only a booked outgoing repayment')
def repayment_only(world, retention_context):
    source = retention_context
    person, payment = world.run(repayment_recipient(source['client'], source['property']))
    world.data.update(retention_person=person, retention_payment=payment)


@when("the administrator reviews that person's erasure")
def review_person(world):
    person = world.data['retention_person']['id']
    answer = world.act('GET', f'/api/dsgvo/persons/{person}/status')
    assert answer.status_code == 200, world.describe_response()
    world.data['person_retention_status'] = answer.json()


@then("the repayment is the person's sole retention reason")
def repayment_binding(world):
    status, payment = world.data['person_retention_status'], world.data['retention_payment']
    assert not status['has_active_relationship']
    assert status['decision'] == 'RESTRICT'
    assert status['bindings'] == [{'kind': 'BOOKED_PAYMENT', 'entity_type': 'payment',
        'entity_id': payment['id'], 'retention_until': payment['retention_until']}]


@when('the administrator requests erasure of the repayment recipient')
@when('the administrator requests erasure of the supplier')
def erase_recipient(world):
    person = world.data['retention_person']['id']
    answer = world.act('POST', f'/api/dsgvo/persons/{person}/request-deletion')
    assert answer.status_code == 200, world.describe_response()
    world.data['person_erasure'] = answer.json()


@then('the person is restricted and the repayment still identifies its recipient')
def restricted_recipient(world):
    person, payment = world.data['retention_person'], world.data['retention_payment']
    assert world.data['person_erasure']['applied'] == 'RESTRICTED'
    answer = world.act('GET', f"/api/dsgvo/persons/{person['id']}/export")
    assert answer.status_code == 200, world.describe_response()
    assert answer.json()['person']['last_name'] == person['last_name']
    answer = world.act('GET', f"/api/payments/{payment['id']}")
    assert answer.status_code == 200, world.describe_response()
    assert answer.json() == payment
