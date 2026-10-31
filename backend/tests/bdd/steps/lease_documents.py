"""Lease documents are created and downloaded through the real HTTP API."""
from uuid import uuid4

from pytest_bdd import given, when, then

from tests.bdd.steps.browser_setup import rental, create
from tests.test_letters import pdf_text


@given('a lease with a landlord, a tenant and a unit meter')
def lease_document_setup(world, seed_data):
    rental(world, seed_data)
    landlord = create(world, '/api/persons', {'first_name': 'Frida', 'last_name': 'Sonnenhof',
        'street': 'Sonnenweg 8', 'zip_code': '12345', 'city': 'Teststadt'})
    domain = create(world, '/api/person-data-domains', {'person_id': landlord['id'], 'reason_code': 'INITIAL_SETUP'}, keyed=True)
    create(world, f"/api/person-data-domains/{domain['id']}/grants", {'user_id': str(seed_data['user_a_admin_id']),
        'capabilities': ['read', 'prepare', 'export'], 'reason_code': 'TEAM_ACCESS'}, keyed=True)
    owner = create(world, '/api/owner-profiles', {'person_id': landlord['id']})
    create(world, '/api/ownership', {'unit_id': world.data['units'][0]['id'],
                                   'owner_profile_id': owner['id'], 'valid_from': '2025-01-01'})
    meter = create(world, '/api/meters', {'property_id': world.data['property']['id'],
        'unit_id': world.data['units'][0]['id'], 'meter_number': 'WASSER-37', 'meter_type': 'KALTWASSER'})
    create(world, f"/api/meters/{meter['id']}/readings", {'reading_date': '2025-12-31', 'value': '100'})
    world.data['meter'] = meter


@when('the landlord creates a lease contract')
def create_contract(world):
    world.act('POST', f"/api/leases/{world.data['leases'][0]['id']}/contract", json={
        'request_id': str(uuid4()), 'as_of': '2026-01-01', 'handover_date': '2026-01-02',
        'agreements': 'Freitext: Nutzung des Gartens gesondert prüfen.',
    })
    assert world.response.status_code == 201, world.describe_response()
    world.data['document'] = world.response.json()


@then('the contract PDF contains the parties, unit and rent')
def contract_content(world):
    pdf = world.look(world.actor, world.data['document']['pdf_url'])
    assert pdf.status_code == 200, pdf.text
    text = pdf_text(pdf.content)
    for expected in ('Mietvertrag', 'Frida Sonnenhof', world.data['people'][0].name, 'Wohnung 1', '700,00', '150,00',
                     'Vermieter', 'Mieter', 'Vorlage; rechtliche Prüfung erforderlich'):
        assert expected in text


@when('the manager records an incoming handover with a reading of 125.5000')
def incoming_handover(world):
    world.act('POST', f"/api/leases/{world.data['leases'][0]['id']}/handover", json={
        'request_id': str(uuid4()), 'date': '2026-01-02', 'type': 'Einzug',
        'parties': 'Ada Synthetisch und Vermieter', 'signatures': 'Ada Synthetisch; Vermieter',
        'readings': [{'meter_id': world.data['meter']['id'], 'value': '125.5000'}],
        'keys': [{'type': 'Haustür', 'count': 3}], 'notes': 'Kratzer im Fensterrahmen',
    })
    assert world.response.status_code == 201, world.describe_response()
    world.data['document'] = world.response.json()


@then('the handover PDF lists the meter and its reading')
def handover_content(world):
    pdf = world.look(world.actor, world.data['document']['pdf_url'])
    assert pdf.status_code == 200, pdf.text
    content = pdf_text(pdf.content)
    for expected in ('Übergabeprotokoll', 'Einzug', 'WASSER-37', '125,5000', 'Haustür', 'Kratzer'):
        assert expected in content


@then('the meter history contains the handover reading')
def handover_reading(world):
    response = world.look(world.actor, f"/api/meters/{world.data['meter']['id']}/readings")
    assert response.status_code == 200, response.text
    assert response.json()[-1]['value'] == '125.5000'
    assert response.json()[-1]['period'] == 'Übergabeprotokoll 2026-01-02'


@when("another organization's administrator requests its lease documents")
def foreign_lease_documents(world):
    foreign = world.administrator_of('Verwaltung Beta')
    root = f"/api/leases/{world.data['leases'][0]['id']}"
    requests = [('GET', root + '/documents', None),
        ('POST', root + '/contract', {'request_id': str(uuid4()), 'as_of': '2026-01-01', 'handover_date': '2026-01-02'}),
        ('POST', root + '/handover', {'request_id': str(uuid4()), 'date': '2026-01-02', 'type': 'Einzug',
                                    'parties': 'Synthetic parties', 'signatures': 'Synthetic names'})]
    world.data['foreign_document_responses'] = [world.send(foreign, method, url, **({'json': body} if body else {}))
                                               for method, url, body in requests]


@then('the lease documents are reported as not found')
def foreign_lease_documents_absent(world):
    assert [response.status_code for response in world.data['foreign_document_responses']] == [404, 404, 404]
    own = world.look(world.actor, f"/api/leases/{world.data['leases'][0]['id']}/documents")
    assert own.status_code == 200 and own.json() == []
