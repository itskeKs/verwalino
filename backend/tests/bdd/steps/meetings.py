"""Resolution register steps through the HTTP API."""
from io import BytesIO

import pdfplumber
from pytest_bdd import given, when, then


@given('Gartenstraße 5 has a held meeting on 15 June 2026 with a resolution')
def register_meeting(world):
    def create(path, payload):
        response = world.act('POST', path, json=payload)
        assert response.status_code == 201, world.describe_response()
        return response.json()
    prop = create('/api/properties', {'name': 'Gartenstraße 5', 'management_type': 'WEG'})
    meeting = create('/api/meetings', {'property_id': prop['id'], 'meeting_date': '2026-06-15',
        'meeting_type': 'ORDENTLICH', 'location': 'Gemeinschaftsraum Gartenstraße 5'})
    response = world.act('POST', f"/api/meetings/{meeting['id']}/hold")
    assert response.status_code == 200, world.describe_response()
    resolution = create("/api/meetings/resolutions", {
        'meeting_id': meeting['id'],
        'wording': 'Die Gartenpflege wird beschlossen.', 'votes_yes': 5, 'votes_no': 0,
        'votes_abstain': 0, 'result': 'ANGENOMMEN'})
    world.data['register_property'] = prop['id']
    world.data['register_resolution'] = resolution['id']


@when('the manager enters that resolution into the register')
def register_resolution(world):
    response = world.act('POST', f"/api/meetings/resolutions/{world.data['register_resolution']}/register")
    assert response.status_code == 201, world.describe_response()
    world.data['register_entry'] = response.json()


@then('the register shows 15 June 2026 as the meeting date and a separate entry date')
def register_dates(world):
    entry = world.data['register_entry']
    assert entry['resolution_date'] == '2026-06-15'
    assert entry['entry_date'] != entry['resolution_date']
    response = world.act('GET', f"/api/meetings/register/{world.data['register_property']}")
    assert response.status_code == 200, world.describe_response()
    assert response.json() == [entry]


@then('the register extract contains the meeting date and place')
def register_extract(world):
    response = world.act('GET', f"/api/meetings/register/{world.data['register_property']}/pdf")
    assert response.status_code == 200, world.describe_response()
    with pdfplumber.open(BytesIO(response.content)) as pdf:
        text = ' '.join(page.extract_text() or '' for page in pdf.pages)
    assert '15.06.2026' in text
    assert 'Gemeinschaftsraum' in text and 'Gartenstraße 5' in text
