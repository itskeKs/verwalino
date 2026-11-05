from decimal import Decimal
from pytest_bdd import given, when, then, parsers
from tests.persons_key_support import persons_property, persons_run, BERND_HOUSEHOLD
from tests.financial_coverage_helpers import attest_fixture


@given('Anna has two persons from January and three from June, while Bernd moves in alone in March')
def persons_house(world):
    world.data['persons_house'] = world.run(persons_property(world.actor))


@when('the manager calculates both 2026 persons-key statements for 1250 euros')
def persons_statements(world):
    data = world.data['persons_house']
    runs = []
    for bernd in (False, True):
        run = world.run(persons_run(world.actor, data, bernd=bernd))
        world.run(attest_fixture(world.actor, 'settlement', run['id'], {
            'advance_payments_total': '0.00', 'advance_payments_source': 'MANUAL',
            'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': 'MANUAL'}))
        response = world.act('POST', f"/api/settlements/{run['id']}/finalize", json={})
        assert response.status_code == 200, world.describe_response()
        runs.append(response.json())
    world.data['persons_runs'] = runs


@then('Anna pays 901.45 euros for 944 person-days and Bernd pays 292.21 euros for 306 person-days')
def persons_totals(world):
    for run, amount, days in zip(world.data['persons_runs'], ('901.45', '292.21'), ('944.000000', '306.000000')):
        assert run['total_cost'] == amount
        assert run['lines'][0]['distribution_base_share'] == days
        assert run['lines'][0]['distribution_base_total'] == '1309.000000'
        assert 'Personentage' in run['lines'][0]['note']


@then("the owner's 59 vacancy person-days cost 56.34 euros and all shares total 1250 euros")
def persons_owner(world):
    runs = world.data['persons_runs']
    response = world.act('GET', f"/api/settlements/{runs[0]['id']}")
    assert response.status_code == 200, world.describe_response()
    vacancy = response.json()['vacancy_attribution']
    assert [(row['unit_number'], row['person_days'], row['amount']) for row in vacancy] == [('2', 59, '56.34')]
    assert sum(Decimal(r['total_cost']) for r in runs) + sum(Decimal(v['amount']) for v in vacancy) == Decimal('1250.00')



@when("the manager deletes Bernd's occupied household row")
def persons_delete(world):
    data = world.data['persons_house']
    response = world.act('PUT', f"/api/units/{data['second']['id']}/household", json=BERND_HOUSEHOLD[:1])
    assert response.status_code == 200, world.describe_response()
    world.data['persons_draft'] = world.run(persons_run(world.actor, data, bernd=True))


@then('the persons-key draft requires the missing count from 1 March 2026')
def persons_missing(world):
    response = world.act('POST', f"/api/settlements/{world.data['persons_draft']['id']}/compute", json={})
    assert response.status_code == 422, world.describe_response()
    assert response.json()['detail'] == 'Handeingabe erforderlich: Personenzahl für Einheit 2 ab 01.03.2026 fehlt.'


@given("a persons-key property has no saved households for Bernd's unit")
def persons_editor_house(world):
    from tests.persons_key_support import ANNA_HOUSEHOLD
    data = world.run(persons_property(world.actor, save_households=False))
    response = world.act('PUT', f"/api/units/{data['unit']['id']}/household", json=ANNA_HOUSEHOLD)
    assert response.status_code == 200, world.describe_response()
    world.data['persons_house'] = data


@when(parsers.parse("the manager enters Bernd's occupied household and saves the editable vacancy suggestion at {width:d} pixels"))
def persons_edit(world, browser_world, width):
    data = world.data['persons_house']
    page = browser_world.page
    async def edit():
        from playwright.async_api import expect
        await page.set_viewport_size({'width': width, 'height': 1000})
        await page.goto(f"/objekte/{data['property']['id']}/einheiten/{data['second']['id']}")
        await expect(page.get_by_role('heading', name='Einheit 2', exact=True)).to_be_visible()
        await page.get_by_role('button', name='Haushalt bearbeiten').click()
        await page.get_by_role('button', name='Leerstand hinzufügen').click()
        await expect(page.get_by_label('Personen im Leerstand ab', exact=True)).to_have_value('')
        await page.get_by_label('Personen im Leerstand ab', exact=True).fill('2026-01-01')
        await expect(page.get_by_label('Personenzahl Leerstand', exact=True)).to_have_value('1')
        await page.get_by_role('button', name='Belegung hinzufügen').click()
        await page.get_by_label('Personen im Haushalt ab', exact=True).fill('2026-03-01')
        await page.get_by_label('Personenzahl Belegung', exact=True).fill('1')
        await page.get_by_role('button', name='Haushalt speichern').click()
        await expect(page.get_by_role('status').filter(has_text='Haushalt gespeichert.')).to_be_visible()
    browser_world.run(edit())


@then('the saved unit editor retains both household streams after reloading')
def persons_editor_saved(world, browser_world):
    from playwright.async_api import expect
    async def verify():
        page = browser_world.page
        await page.reload()
        await expect(page.get_by_text('1 Person ab 01.03.2026', exact=True)).to_be_visible()
        await expect(page.get_by_text('Leerstand: 1 Person ab 01.01.2026', exact=True)).to_be_visible()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        import os
        from pathlib import Path
        if os.environ.get('VERWALINO_CAPTURE_DESIGN'):
            root = Path(__file__).resolve().parents[4] / 'docs/assets/design'
            await page.screenshot(path=str(root / f'persons-household-after-{page.viewport_size["width"]}.png'), full_page=True)
    browser_world.run(verify())
    data = world.data['persons_house']
    assert world.act('GET', f"/api/units/{data['second']['id']}/household").json() == list(reversed(BERND_HOUSEHOLD))


@then("Bernd's statement uses the saved household and costs 292.21 euros")
def persons_editor_statement(world, browser_world):
    run = world.run(persons_run(world.actor, world.data['persons_house'], bernd=True))
    response = world.act('POST', f"/api/settlements/{run['id']}/compute", json={})
    assert response.status_code == 200, world.describe_response()
    assert response.json()['total_cost'] == '292.21'
    async def verify():
        import os
        from pathlib import Path
        from playwright.async_api import expect
        page = browser_world.page
        await page.goto(f"/abrechnungen/{run['id']}")
        await page.get_by_role('button', name='Strukturierte Eingaben anzeigen').click()
        await expect(page.get_by_text('Personentage für Einheit 2: 306 von 1.309', exact=True)).to_be_visible()
        await expect(page.get_by_role('link', name='Haushalt für Einheit 2 bearbeiten')).to_be_visible()
        if os.environ.get('VERWALINO_CAPTURE_DESIGN'):
            root = Path(__file__).resolve().parents[4] / 'docs/assets/design'
            await page.screenshot(path=str(root / f'persons-statement-after-{page.viewport_size["width"]}.png'), full_page=True)
    browser_world.run(verify())


@when(parsers.parse("the manager opens Anna's finalized statement at {width:d} pixels"))
def open_frozen_vacancy(world, browser_world, width):
    async def open_statement():
        from playwright.async_api import expect
        page = browser_world.page
        await page.set_viewport_size({'width': width, 'height': 1000})
        await page.goto(f"/abrechnungen/{world.data['persons_runs'][0]['id']}")
        heading = page.get_by_role('heading', name='Abrechnungsergebnis', exact=True)
        await expect(heading).to_be_visible()
        await heading.scroll_into_view_if_needed()
    browser_world.run(open_statement())


@then("the statement displays unit 2's 59 vacancy person-days and 56.34 euros for the owner")
def visible_vacancy(world, browser_world):
    async def read_statement():
        from playwright.async_api import expect
        section = browser_world.page.get_by_role('region', name='Leerstand – Eigentümeranteile', exact=True)
        await expect(section).to_be_visible()
        row = section.get_by_role('listitem').filter(has_text='Einheit 2')
        await expect(row).to_contain_text('59 Personentage')
        await expect(row).to_contain_text('56,34')
        await expect(row).to_contain_text('€')
        await section.scroll_into_view_if_needed()
        assert await browser_world.page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        import os
        from pathlib import Path
        if os.environ.get('VERWALINO_CAPTURE_DESIGN'):
            width = browser_world.page.viewport_size['width']
            await browser_world.page.screenshot(path=str(Path(__file__).resolve().parents[4] / 'docs/assets/design' / f'persons-vacancy-after-{width}.png'))
    browser_world.run(read_statement())
