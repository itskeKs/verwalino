"""Lease documents are created and downloaded through the real HTTP API."""
from uuid import uuid4

from pytest_bdd import given, when, then, parsers

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


@given('the rent changes to 750 net plus 150 operating and 90 heating on 15 March 2026')
def dated_heating_rate(world):
    create(world, f"/api/leases/{world.data['leases'][0]['id']}/rent-changes", {
        'valid_from': '2026-03-15', 'base_rent': '750.00',
        'advance_payment': '150.00', 'heating_advance_payment': '90.00',
    })


@when(parsers.parse('the manager creates a contract and rent letter dated {day}'))
def dated_rent_documents(world, day):
    contract = create(world, f"/api/leases/{world.data['leases'][0]['id']}/contract", {
        'request_id': str(uuid4()), 'as_of': day, 'handover_date': '2026-01-02',
    })
    template = create(world, '/api/letters/templates', {
        'name': 'Mietaufstellung', 'subject': 'Miete am {{datum}}', 'body': '{{vertrag.mietaufstellung}}',
    })
    world.data['rent_template'] = template['id']
    letters = create(world, '/api/letters/issue', {
        'request_id': str(uuid4()), 'as_of': day, 'property_id': world.data['property']['id'],
        'template_id': template['id'],
    })
    assert len(letters) == 1
    world.data['rent_originals'] = {}
    for row in (contract, letters[0]):
        pdf = world.look(world.actor, row['pdf_url'])
        assert pdf.status_code == 200, pdf.text
        world.data['rent_originals'][row['pdf_url']] = pdf.content


@then(parsers.parse('both PDFs show {net} net and {operating} operating advance'))
def dated_rent_amounts(world, net, operating):
    for original in world.data['rent_originals'].values():
        content = pdf_text(original)
        assert f': {net} €' in content
        assert f': {operating} €' in content


@then('both PDFs show 90,00 heating and 990,00 total rent')
def dated_rent_heating_total(world):
    for original in world.data['rent_originals'].values():
        content = pdf_text(original)
        for expected in ('Stand 15.03.2026', 'Betriebskosten-Vorauszahlung: 150,00 €',
                         'Heizkosten-Vorauszahlung: 90,00 €', 'Monatliche Gesamtmiete: 990,00 €'):
            assert expected in content
        assert 'Originalvertrag' not in content


@then('both PDFs keep the original single advance without heating')
def dated_rent_no_heating(world):
    for original in world.data['rent_originals'].values():
        content = pdf_text(original)
        assert 'Monatliche Kaltmiete (Originalvertrag): 700,00 €' in content
        assert 'Monatliche Vorauszahlung (Originalvertrag): 150,00 €' in content
        assert 'Heizkosten' not in content


@when('the rent changes again on 1 July 2026')
def later_document_rate(world):
    create(world, f"/api/leases/{world.data['leases'][0]['id']}/rent-changes", {
        'valid_from': '2026-07-01', 'base_rent': '800.00',
        'advance_payment': '160.00', 'heating_advance_payment': '100.00',
    })


@then('the issued contract and rent letter still download byte-identically')
def unchanged_rent_originals(world):
    for url, original in world.data['rent_originals'].items():
        response = world.look(world.actor, url)
        assert response.status_code == 200, response.text
        assert response.content == original


def april_contract(world):
    row = create(world, f"/api/leases/{world.data['leases'][0]['id']}/contract", {
        'request_id': str(uuid4()), 'as_of': '2026-04-01', 'handover_date': '2026-01-02',
    })
    response = world.look(world.actor, row['pdf_url'])
    assert response.status_code == 200, response.text
    return row['pdf_url'], response.content


@when('the manager ends the tenancy on 28 February after issuing an April contract')
def end_tenancy_after_issuing(world):
    world.data['pre_termination_contract'] = april_contract(world)
    end_tenancy(world)


@when('the manager ends the tenancy on 28 February')
def end_tenancy(world):
    response = world.act('PATCH', f"/api/leases/{world.data['leases'][0]['id']}", json={'valid_to': '2026-02-28'})
    assert response.status_code == 200, response.text
    assert response.json()['valid_to'] == '2026-02-28'


@when('the manager creates a new contract dated 1 April')
def create_historical_contract(world):
    _, world.data['historical_contract'] = april_contract(world)


@then('the new contract shows the rent at the end of the tenancy')
def historical_contract_amounts(world):
    assert_end_rent(pdf_text(world.data['historical_contract']), '01.04.2026')


def assert_end_rent(content, document_date):
    assert '990,00 €' not in content, 'The scheduled March increase never applied during the tenancy'
    for expected in (document_date, 'Stand 28.02.2026', '700,00 €', '150,00 €', 'Monatliche Gesamtmiete: 850,00 €'):
        assert expected in content
    assert 'Heizkosten' not in content


@then('both PDFs show the rent at the end of the tenancy on 28 February')
def last_day_amounts(world):
    for original in world.data['rent_originals'].values():
        assert_end_rent(pdf_text(original), '28.02.2026')


@then('a circular letter dated 1 April has no eligible recipients')
def ended_tenant_excluded(world):
    response = world.act('POST', '/api/letters/issue', json={
        'request_id': str(uuid4()), 'as_of': '2026-04-01', 'property_id': world.data['property']['id'],
        'template_id': world.data['rent_template'],
    })
    assert response.status_code == 422, response.text
    assert response.json()['detail'] == 'Keine Mieter am gewählten Stichtag'


@then('the pre-termination contract still downloads byte-identically')
def unchanged_pre_termination_contract(world):
    url, original = world.data['pre_termination_contract']
    response = world.look(world.actor, url)
    assert response.status_code == 200, response.text
    assert response.content == original


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
