"""Special-levy authority and frozen shares through the ordinary API and UI."""
import os
import re
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from playwright.async_api import expect
from pytest_bdd import given, when, then, parsers

from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.records import create_person
from tests.bdd.steps.browser_common import browser_step


def create_owner(world, user, first):
    """An owner profile of a new person the signed-in user may read, prepare and export."""
    person = create(world, '/api/persons', {'first_name': first, 'last_name': 'Berg'})
    domain = create(world, '/api/person-data-domains', {'person_id': person['id'], 'reason_code': 'INITIAL_SETUP'}, keyed=True)
    create(world, f"/api/person-data-domains/{domain['id']}/grants", {'user_id': user,
        'capabilities': ['read', 'prepare', 'export'], 'reason_code': 'TEAM_ACCESS'}, keyed=True)
    return create(world, '/api/owner-profiles', {'person_id': person['id']})


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
        'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'confirm', 'export'], 'reason': 'Synthetic special levy',
    }, keyed=True)
    owners = []
    units = []
    for number, mea, first in [(1, '600', 'Anna'), (2, '400', 'Berta')]:
        unit = create(world, '/api/units', {'property_id': prop['id'], 'number': str(number), 'usage_type': 'WOHNUNG', 'mea': mea, 'area': '70'})
        owner = create_owner(world, user, first)
        create(world, '/api/ownership', {'unit_id': unit['id'], 'owner_profile_id': owner['id'], 'valid_from': '2026-01-01'})
        owners.append(owner)
        units.append(unit)
    meeting = create(world, '/api/meetings', {'property_id': prop['id'], 'meeting_date': '2026-05-15',
        'meeting_type': 'ORDENTLICH', 'location': 'Gemeinschaftsraum Gartenstraße 5'})
    for title in ['Begrüßung', 'Bericht der Verwaltung', 'Dachreparatur']:
        agenda = create(world, f"/api/meetings/{meeting['id']}/agenda", {'meeting_id': meeting['id'], 'title': title})
    assert world.act('POST', f"/api/meetings/{meeting['id']}/hold").status_code == 200
    resolution = create(world, '/api/meetings/resolutions', {'meeting_id': meeting['id'], 'agenda_item_id': agenda['id'],
        'wording': 'Dachreparatur durch Sonderumlage von 1.200,00 € finanzieren.',
        'votes_yes': 2, 'votes_no': 0, 'votes_abstain': 0, 'result': 'ANGENOMMEN'})
    entry = create(world, f"/api/meetings/resolutions/{resolution['id']}/register", {})
    world.data.update(property=prop, scope=scope, levy_owners=owners, levy_units=units, levy_user=user,
        levy_resolution=resolution, levy_entry=entry)


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


@then('the Mahnlauf asks for an accounting scope')
def mahnlauf_asks_for_scope(world):
    answer = world.act('GET', '/api/fristen/context')
    assert answer.status_code == 200 and answer.json() == {'requires_accounting_scope': True}, world.describe_response()


@then('an overview opened without an accounting scope is explained in German')
def unscoped_overview_explained(world):
    answer = world.act('GET', '/api/payments/open-items', params={'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    assert answer.status_code == 409, world.describe_response()
    assert answer.json()['detail'] == ('Wählen Sie einen eindeutigen Abrechnungskreis und prüfen Sie noch nicht '
                                       'zugeordnete Daten, bevor Sie die Zahlungen berechnen.')


@given('the organization holds a frozen special levy')
def levy_elsewhere(world, seed_data):
    """Gartenstraße 5 and its roof repair levy next to the scenario's own property and accounting scope."""
    own = {key: world.data[key] for key in ('property', 'scope')}
    roof_repair(world, seed_data)
    create_roof_levy(world)
    world.data.update(own)


@given('Sonnenweg 5 has no accounting scope and its tenant owes 700 euros rent for January 2026')
def rental_without_scope(world, seed_data):
    user = str(seed_data['user_a_admin_id'])
    prop = create(world, '/api/properties', {
        'name': 'Sonnenweg 5', 'management_type': 'MIET', 'street': 'Sonnenweg 5', 'zip_code': '12345', 'city': 'Teststadt',
        'data_audience': {'label': f'Sonnenweg {uuid4()}', 'reason': 'Synthetic special levy',
            'grants': [{'user_id': user, 'capabilities': ['read', 'prepare', 'approve', 'export'], 'reason': 'Synthetic special levy'}]},
    }, keyed=True)
    unit = create(world, '/api/units', {'property_id': prop['id'], 'number': '1', 'usage_type': 'WOHNUNG', 'area': '70'})
    person = create_person(world, 'Verwaltung Alpha', 'Synthetisch')
    domain = create(world, '/api/person-data-domains', {'person_id': person.id, 'reason_code': 'INITIAL_SETUP'}, keyed=True)
    create(world, f"/api/person-data-domains/{domain['id']}/grants", {'user_id': user,
        'capabilities': ['read', 'prepare', 'export'], 'reason_code': 'TEAM_ACCESS'}, keyed=True)
    tenant = create(world, '/api/tenant-profiles', {'person_id': person.id})
    lease = create(world, '/api/leases', {'unit_id': unit['id'], 'tenant_profile_id': tenant['id'],
        'valid_from': '2026-01-01', 'base_rent': '700.00', 'advance_payment': '0.00'})
    world.data.update(plain_tenant=tenant, plain_lease=lease)


JANUARY = {'period_start': '2026-01-01', 'period_end': '2026-01-31'}


@then('the rent account and the open items of that tenant show 700 euros open for January 2026')
def rent_account_without_scope(world):
    lease = world.data['plain_lease']['id']
    account = world.act('GET', f'/api/payments/mietkonto/lease/{lease}', params=JANUARY)
    assert account.status_code == 200, world.describe_response()
    assert (account.json()['soll_total'], account.json()['ist_total'], account.json()['balance']) == ('700.00', '0.00', '700.00')
    items = world.act('GET', f'/api/payments/open-items/lease/{lease}', params=JANUARY)
    assert items.status_code == 200, world.describe_response()
    assert [(row['payment_type'], row['open_amount']) for row in items.json()] == [('KALTMIETE', '700.00')]


@then('the manager can create a tenant notice over 700 euros for that rent')
def notice_without_scope(world):
    answer = world.act('POST', '/api/dunning/tenant', json={
        'tenant_profile_id': world.data['plain_tenant']['id'], 'lease_agreement_id': world.data['plain_lease']['id'],
        **JANUARY, 'reference_date': '2026-03-02'})
    assert answer.status_code == 201, world.describe_response()
    assert (answer.json()['principal_amount'], answer.json()['total_amount']) == ('700.00', '700.00')


@when("Berta's ownership entry is corrected to start on 1 August 2026")
def correct_berta_start(world):
    berta = world.data['levy_owners'][1]['id']
    rows = world.act('GET', '/api/ownership').json()
    entry = next(row for row in rows if row['owner_profile_id'] == berta)
    answer = world.act('POST', f"/api/ownership/{entry['id']}/correction",
                       json={'valid_from': '2026-08-01', 'mea_share': entry['mea_share']})
    assert answer.status_code == 201, world.describe_response()


@when('the manager requests the roof repair levy due on 1 July 2026')
def request_roof_levy(world):
    world.act('POST', '/api/special-levies', json={**levy_payload(world), 'resolution_id': world.data['levy_resolution']['id']})


@then('the levy is refused because a unit has no owner on its due date')
def levy_needs_due_date_owner(world):
    assert world.response.status_code == 422, world.describe_response()
    assert world.response.json()['detail'] == 'Every unit needs positive MEA and due-date ownership'


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


MONTHS = {'June': 6, 'August': 8}


@when(parsers.parse("Berta's unit is sold to Clara from {day:d} {month} {year:d}, recorded after the levy"))
def sale_recorded_after_the_levy(world, day, month, year):
    unit = world.data['levy_units'][1]['id']
    first_day = date(year, MONTHS[month], day)
    berta = world.data['levy_owners'][1]['id']
    entry = next(row for row in world.act('GET', '/api/ownership', params={'unit_id': unit}).json()
                 if row['owner_profile_id'] == berta)
    answer = world.act('PATCH', f"/api/ownership/{entry['id']}", json={'valid_to': (first_day - timedelta(days=1)).isoformat()})
    assert answer.status_code == 200, world.describe_response()
    clara = create_owner(world, world.data['levy_user'], 'Clara')
    create(world, '/api/ownership', {'unit_id': unit, 'owner_profile_id': clara['id'], 'valid_from': first_day.isoformat()})
    world.data['levy_buyer'] = clara


def levy_amounts(world):
    answer = world.act('GET', '/api/special-levies', params={'accounting_scope_id': world.data['scope']['id']})
    assert answer.status_code == 200, world.describe_response()
    (levy,) = answer.json()
    return {row['owner_profile_id']: row['amount'] for row in levy['allocations']
            if row['unit_id'] == world.data['levy_units'][1]['id']}


@then(parsers.parse('the levy shows 480 euros for {recipient} and nothing for {other}'))
def levy_names_only_the_owner_on_the_due_date(world, recipient, other):
    people = {'Berta': world.data['levy_owners'][1]['id'], 'Clara': world.data.get('levy_buyer', {}).get('id')}
    assert levy_amounts(world) == {people[recipient]: '480.00'}
    assert people[other] not in levy_amounts(world)


@then("Clara's owner account shows 480 euros due and Berta's shows nothing due")
def owner_accounts_follow_the_levy(world):
    for owner, expected in ((world.data['levy_buyer'], '480.00'), (world.data['levy_owners'][1], '0.00')):
        answer = world.act('GET', f"/api/payments/mietkonto/owner/{owner['id']}", params={
            'accounting_scope_id': world.data['scope']['id'], 'period_start': '2026-07-01', 'period_end': '2026-07-31'})
        assert answer.status_code == 200, world.describe_response()
        assert answer.json()['balance'] == expected


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
