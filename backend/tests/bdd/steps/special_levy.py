"""Special-levy authority and frozen shares through the ordinary API and UI."""
import os
import re
from pathlib import Path
from uuid import uuid4

from playwright.async_api import expect
from pytest_bdd import given, when, then, parsers

from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.browser_common import browser_step


@given('Gartenstraße 5 has two owners and an adopted roof repair resolution')
def roof_repair(world, seed_data):
    user = str(seed_data['user_a_admin_id'])
    prop = create(world, '/api/properties', {
        'name': 'Gartenstraße 5', 'management_type': 'WEG',
        'data_audience': {'label': f'Roof repair {uuid4()}', 'reason': 'Synthetic special levy',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'], 'reason': 'Synthetic special levy'}]},
    }, keyed=True)
    answer = world.act('POST', f"/api/properties/{prop['id']}/mea-scale/confirm", json= {'denominator': 1000, 'expected_scale_status': 'LEGACY_UNCONFIRMED', 'confirm_existing_values_as_numerators': True})
    assert answer.status_code == 200, world.describe_response()
    scope = create(world, '/api/accounting-scopes', {
        'property_id': prop['id'], 'scope_type': 'WEG', 'label': 'WEG Gartenstraße 5',
        'valid_from': '2026-01-01', 'whole_property': True,
    }, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {
        'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'], 'reason': 'Synthetic special levy',
    }, keyed=True)
    owners = []
    for number, mea, first in [(1, '600', 'Anna'), (2, '400', 'Berta')]:
        unit = create(world, '/api/units', {'property_id': prop['id'], 'number': str(number), 'usage_type': 'WOHNUNG', 'mea': mea, 'area': '70'})
        person = create(world, '/api/persons', {'first_name': first, 'last_name': 'Berg'})
        domain = create(world, '/api/person-data-domains', {'person_id': person['id'], 'reason_code': 'INITIAL_SETUP'}, keyed=True)
        create(world, f"/api/person-data-domains/{domain['id']}/grants", {'user_id': user,
            'capabilities': ['read', 'prepare', 'export'], 'reason_code': 'TEAM_ACCESS'}, keyed=True)
        owner = create(world, '/api/owner-profiles', {'person_id': person['id']})
        create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': owner['id'], 'valid_from': '2026-01-01'})
        owners.append(owner)
    meeting = create(world, '/api/meetings', {'property_id': prop['id'], 'meeting_date': '2026-05-15',
        'meeting_type': 'ORDENTLICH', 'location': 'Gemeinschaftsraum Gartenstraße 5'})
    for title in ['Begrüßung', 'Bericht der Verwaltung', 'Dachreparatur']:
        agenda = create(world, f"/api/meetings/{meeting['id']}/agenda", {'meeting_id': meeting['id'], 'title': title})
    assert world.act('POST', f"/api/meetings/{meeting['id']}/hold").status_code == 200
    resolution = create(world, '/api/meetings/resolutions', {'meeting_id': meeting['id'], 'agenda_item_id': agenda['id'],
        'wording': 'Dachreparatur durch Sonderumlage von 1.200,00 € finanzieren.',
        'votes_yes': 2, 'votes_no': 0, 'votes_abstain': 0, 'result': 'ANGENOMMEN'})
    entry = create(world, f"/api/meetings/resolutions/{resolution['id']}/register", {})
    world.data.update(property=prop, scope=scope, levy_owners=owners, levy_resolution=resolution, levy_entry=entry)


def levy_payload(world):
    return {'property_id': world.data['property']['id'], 'accounting_scope_id': world.data['scope']['id'],
        'amount': '1200.00', 'due_date': '2026-07-01'}


@when('the manager requests a special levy without selecting a resolution')
def missing_reference(world):
    world.act('POST', '/api/special-levies', json=levy_payload(world))


@then('the manager is told that a special levy needs a resolution')
def missing_refusal(world):
    assert world.response.status_code == 422, world.describe_response()
    assert world.response.json()['detail'] == 'Eine Sonderumlage braucht einen Beschluss.'


@then('no special levy has been created')
def no_levy(world):
    answer = world.act('GET', '/api/special-levies', params={'accounting_scope_id': world.data['scope']['id']})
    assert answer.status_code == 200 and answer.json() == [], world.describe_response()


@when('the manager creates the roof repair levy due on 1 July 2026')
def create_roof_levy(world):
    world.data['levy'] = create(world, '/api/special-levies', {**levy_payload(world), 'resolution_id': world.data['levy_resolution']['id']})


@then('the owner shares are 720 euros and 480 euros due on 1 July 2026')
def roof_shares(world):
    levy = world.data['levy']
    assert levy['due_date'] == '2026-07-01'
    assert sorted(row['amount'] for row in levy['allocations']) == ['480.00', '720.00']
    for owner, expected in zip(world.data['levy_owners'], ['720.00', '480.00'], strict=True):
        answer = world.act('GET', f"/api/payments/mietkonto/owner/{owner['id']}", params={
            'accounting_scope_id': world.data['scope']['id'], 'period_start': '2026-07-01', 'period_end': '2026-07-31'})
        assert answer.status_code == 200, world.describe_response()
        assert answer.json()['balance'] == expected


@then('the roof repair register entry shows its levy amount and due date')
def register_levy(world):
    answer = world.act('GET', f"/api/meetings/register/{world.data['property']['id']}")
    assert answer.status_code == 200, world.describe_response()
    entry = next(e for e in answer.json() if e['id'] == world.data['levy_entry']['id'])
    assert entry['special_levies'] == [{'id': world.data['levy']['id'], 'accounting_scope_id': world.data['scope']['id'], 'amount': '1200.00', 'due_date': '2026-07-01'}]


async def capture(browser, screen):
    if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1':
        directory = Path(__file__).resolve().parents[4] / 'docs/assets/design'
        await browser.page.screenshot(path=str(directory / f'levy-{screen}-after-{browser.page.viewport_size["width"]}.png'), full_page=True)


async def prepare_levy_form(browser, width):
    await browser.page.set_viewport_size({'width': width, 'height': 1000})
    await browser.visit('/sonderumlagen')
    await browser.select('WEG-Objekt *', browser.world.data['property']['id'])
    await browser.scope(scope=browser.page.locator('div.card').first)
    form = browser.page.locator('form').filter(has=browser.page.get_by_role('heading', name='Sonderumlage anlegen'))
    await browser.scope(scope=form)
    await form.get_by_label('Betrag (EUR) *', exact=True).fill('1200,00')
    await form.get_by_label('Fällig am *', exact=True).fill('2026-07-01')
    return form


@when(parsers.parse('the manager clicks create without a resolution in the browser at {width:d} pixels'))
@browser_step
async def browser_missing_resolution(browser_world, width):
    browser = browser_world
    form = await prepare_levy_form(browser, width)
    requests = []
    browser.page.on('request', lambda request: requests.append(request)
        if request.method == 'POST' and '/api/special-levies' in request.url else None)
    await form.get_by_role('button', name='Sonderumlage anlegen', exact=True).click()
    browser.world.data['missing_resolution_posts'] = requests


@then('the browser explains the missing resolution and retains the financial inputs')
@browser_step
async def browser_missing_resolution_feedback(browser_world):
    browser = browser_world
    await expect(browser.page.get_by_role('alert')).to_have_text('Eine Sonderumlage braucht einen Beschluss.')
    await expect(browser.page.get_by_label('Beschluss *', exact=True)).to_have_value('')
    await expect(browser.page.get_by_label('Betrag (EUR) *', exact=True)).to_have_value('1200,00')
    await expect(browser.page.get_by_label('Fällig am *', exact=True)).to_have_value('2026-07-01')
    assert browser.world.data['missing_resolution_posts'] == []
    await capture(browser, 'missing-reference')


@when(parsers.parse('the manager creates the roof repair levy in the browser at {width:d} pixels'))
@browser_step
async def browser_create_levy(browser_world, width):
    browser = browser_world
    form = await prepare_levy_form(browser, width)
    await capture(browser, 'page')
    await browser.select('Beschluss *', browser.world.data['levy_resolution']['id'], form)
    async with browser.page.expect_response(lambda r: r.request.method == 'POST' and '/api/special-levies' in r.url) as pending:
        await form.get_by_role('button', name='Sonderumlage anlegen', exact=True).click()
    response = await pending.value
    assert response.status == 201, await response.text()
    browser.world.data['levy'] = await response.json()
    await expect(browser.page.get_by_role('cell', name='Anna Berg', exact=True)).to_be_visible()
    await capture(browser, 'page')


@then('the levy links to the meeting of 15 May and TOP 3')
@browser_step
async def browser_levy_reference(browser_world):
    browser = browser_world
    link = browser.page.get_by_role('link', name='15.05.2026 · TOP 3', exact=True)
    await expect(link).to_be_visible()
    await link.click()
    await expect(browser.page.get_by_role('heading', name='Beschluss-Sammlung', exact=True)).to_be_visible()


@then('the register links back to the levy due on 1 July')
@browser_step
async def browser_register_reference(browser_world):
    browser = browser_world
    link = browser.page.get_by_role('link', name=re.compile(r'Sonderumlage 1\.200,00\s€, fällig 01\.07\.2026'), exact=True)
    await expect(link).to_be_visible()
    await link.scroll_into_view_if_needed()
    await expect(link).to_be_in_viewport()
    await capture(browser, 'register')
    await link.click()
    await expect(browser.page.get_by_role('cell', name='720,00 €', exact=True)).to_be_visible()


@when('Verwaltung Beta tries to create, read and link that special levy')
def foreign_levy_authority(world):
    beta = world.administrator_of('Verwaltung Beta')
    levy = world.data['levy']
    reference = {'resolution_id': world.data['levy_resolution']['id']}
    world.data['foreign_levy_responses'] = [
        world.send(beta, 'POST', '/api/special-levies', json={**levy_payload(world), **reference}),
        world.send(beta, 'GET', '/api/special-levies', params={'accounting_scope_id': levy['accounting_scope_id']}),
        world.send(beta, 'PUT', f"/api/special-levies/{levy['id']}/resolution", json=reference),
        world.send(beta, 'GET', f"/api/meetings/register/{levy['property_id']}"),
    ]


@then('the special levy authority is not found and its frozen obligation is unchanged')
def foreign_levy_refused(world):
    responses = world.data['foreign_levy_responses']
    assert [r.status_code for r in responses[:3]] == [404] * 3
    assert responses[3].status_code == 200 and responses[3].json() == []
    levy = world.data['levy']
    answer = world.act('GET', '/api/special-levies', params={'accounting_scope_id': levy['accounting_scope_id']})
    assert answer.status_code == 200, world.describe_response()
    assert answer.json() == [levy]
