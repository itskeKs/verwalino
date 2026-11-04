"""Person privacy operations use the real HTTP endpoints."""
from pytest_bdd import given, when, then

from tests.person_retention_support import repayment_recipient, manual_cost_recipient


@given('an otherwise inactive supplier has a confirmed 2020 invoice for services in 2017')
def later_invoice(world, rent_world, export_client):
    world.actor = export_client
    person, cost = world.run(manual_cost_recipient(export_client, rent_world['property']))
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
def repayment_only(world, rent_world, export_client):
    world.actor = export_client
    person, payment = world.run(repayment_recipient(export_client, rent_world['property']))
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
