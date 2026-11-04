"""A fresh organization's manager follows the profile hint through real pages."""
import os
from pathlib import Path
from uuid import uuid4

import pytest_asyncio
from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from app.models.organization import Organization
from app.models.user import User
from app.models.membership import Membership
from app.core.security import hash_password
from tests.conftest import _api_test_client
from tests.bdd.steps.browser_common import browser_step, button
from tests.bdd.steps.browser_setup import create

ROOT = Path(__file__).resolve().parents[3]
scenarios(str(ROOT / 'features/journeys/profile_hints.feature'))


@pytest_asyncio.fixture
async def profile_identity(migrator_session):
    """Like the shared seeds, commit an isolated organization visible to the UI."""
    organization = Organization(id=uuid4(), name='Profilhinweise Testverwaltung')
    user = User(id=uuid4(), organization_id=organization.id,
                email=f'profile-hints-{uuid4()}@example.test', display_name='Testverwaltung',
                password_hash=hash_password('testpass1234'))
    migrator_session.add(organization)
    await migrator_session.flush()
    migrator_session.add(user)
    await migrator_session.flush()
    migrator_session.add(Membership(organization_id=organization.id, user_id=user.id, role='OWNER_ADMIN'))
    await migrator_session.commit()
    async with _api_test_client() as client:
        response = await client.post('/api/auth/login', json={'email': user.email, 'password': 'testpass1234'})
        assert response.status_code == 200
        yield client, user


@given('a fresh organization with Anna Beispiel and Bernd Test without profiles')
def unmarked_people(world, profile_identity):
    world.actor, user = profile_identity
    world.data['profile_email'] = user.email
    home = create(world, '/api/properties', {
        'name': 'Sonnenhof', 'management_type': 'MIET',
        'data_audience': {'label': 'Sonnenhof', 'reason': 'Synthetic browser journey',
                          'grants': [{'user_id': str(user.id), 'capabilities': ['read', 'prepare', 'approve', 'export'],
                                      'reason': 'Synthetic browser journey'}]},
    }, keyed=True)
    world.data['profile_property'] = home
    world.data['profile_unit'] = create(world, '/api/units', {
        'property_id': home['id'], 'number': 'Wohnung 1', 'usage_type': 'WOHNUNG',
    })
    for first, last in [('Anna', 'Beispiel'), ('Bernd', 'Test')]:
        person = create(world, '/api/persons', {'first_name': first, 'last_name': last})
        domain = create(world, '/api/person-data-domains', {'person_id': person['id'], 'reason_code': 'INITIAL_SETUP'}, keyed=True)
        create(world, f"/api/person-data-domains/{domain['id']}/grants", {
            'user_id': str(user.id), 'capabilities': ['read', 'prepare', 'export'], 'reason_code': 'TEAM_ACCESS',
        }, keyed=True)


def labels(kind):
    return ('+ Mietvertrag', 'Mieter *', 'Mieter') if kind == 'tenancy' else ('+ Eigentum', 'Eigentuemer *', 'Eigentuemer')


@when(parsers.parse('I follow the empty {kind} hint and mark only Anna at {width:d} px'))
@browser_step
async def follow_hint(browser_world, kind, width):
    b, page = browser_world, browser_world.page
    await page.set_viewport_size({'width': width, 'height': 900})
    await b.context.clear_cookies()
    await b.visit('/objekte')
    await page.get_by_label('E-Mail', exact=True).fill(b.world.data['profile_email'])
    await page.get_by_label('Passwort', exact=True).fill('testpass1234')
    await button(page, 'Anmelden').click()
    await expect(page.get_by_role('link', name='Objekte', exact=True)).to_be_visible()
    d = b.world.data
    await b.visit(f"/objekte/{d['profile_property']['id']}/einheiten/{d['profile_unit']['id']}")
    action, select, role = labels(kind)
    await button(page, action).click()
    await expect(page.get_by_role('combobox', name=select, exact=True).locator('option')).to_have_count(1)
    hint = page.get_by_text('Noch keine Person als', exact=False)
    await page.get_by_role('combobox', name=select, exact=True).scroll_into_view_if_needed()
    if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1':
        state = 'after' if await hint.count() else 'before'
        await page.screenshot(path=str(ROOT / f'docs/assets/design/profile-hints/{state}-{kind}-{width}.png'), full_page=True)
    await expect(hint).to_contain_text('Rollen / Profile')
    await expect(hint).to_contain_text(f'Als {role} kennzeichnen')
    await hint.get_by_role('link', name='Personen', exact=True).click()
    await page.get_by_role('link', name='Anna Beispiel', exact=True).click()
    await expect(page.get_by_role('heading', name='Rollen / Profile', exact=True)).to_be_visible()
    await button(page, f'Als {role} kennzeichnen').click()
    await expect(button(page, f'Als {role} kennzeichnen')).to_have_count(0)


@then(parsers.parse('returning through the app offers Anna but not Bernd for {kind}'))
@browser_step
async def return_to_unit(browser_world, kind):
    page = browser_world.page
    if await button(page, 'Navigation umschalten').is_visible():
        await button(page, 'Navigation umschalten').click()
    await page.get_by_role('link', name='Objekte', exact=True).click()
    await page.get_by_role('link', name='Sonnenhof', exact=True).click()
    await page.get_by_role('link', name='Wohnung 1', exact=True).click()
    action, select, _ = labels(kind)
    await button(page, action).click()
    selection = page.get_by_role('combobox', name=select, exact=True)
    await expect(selection.locator('option')).to_have_text(['-- waehlen --', 'Anna Beispiel'])
    await expect(page.get_by_text('Noch keine Person als', exact=False)).to_have_count(0)
    await selection.select_option(label='Anna Beispiel')
    assert await selection.input_value()
