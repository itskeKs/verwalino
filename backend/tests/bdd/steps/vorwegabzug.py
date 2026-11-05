from decimal import Decimal
import subprocess
from pytest_bdd import given, when, then, parsers
from tests.commercial_preallocation_support import commercial_property, commercial_rule, REASON
from tests.bdd.steps.browser_common import browser_step
from tests.bdd.report import artifact_directory, attach_artifact
from playwright.async_api import expect


@given('a shop of 60 square metres and flats of 75 and 65 square metres with annual waste costs of 1200 euros')
def mixed_waste_property(world):
    world.data['commercial'] = world.run(commercial_property(world.actor))


@when('the manager calculates the statements without a commercial preallocation')
def baseline_statements(world):
    compute_commercial_statements(world)


def compute_commercial_statements(world, rule=None):
    data = world.data['commercial']
    results = []
    for index, row in enumerate(data['rows']):
        body = {'commercial_preallocations': [rule]} if index == 0 and rule else {}
        answer = world.act('POST', f"/api/settlements/{row['run']['id']}/compute", json=body)
        assert answer.status_code == 200, world.describe_response()
        results.append(answer.json())
    world.data['commercial_results'] = results


@when('the manager assigns 50 percent of the waste cost to commerce because of the restaurant waste bins')
def commercial_half(world):
    compute_commercial_statements(world, commercial_rule(world.data['commercial']))


@then(parsers.parse('the shop and flats owe {shop}, {flat_one} and {flat_two} euros'))
def commercial_amounts(world, shop, flat_one, flat_two):
    totals = [Decimal(row['total_cost']) for row in world.data['commercial_results']]
    assert totals == list(map(Decimal, (shop, flat_one, flat_two)))
    assert sum(totals) == Decimal('1200.00')


@then('every finalized statement PDF explains the 600 euro commercial preallocation and its reason')
def commercial_pdfs(world):
    for row in world.data['commercial']['rows']:
        url = f"/api/settlements/{row['run']['id']}"
        answer = world.act('POST', url + '/finalize', json={})
        assert answer.status_code == 200, world.describe_response()
        assert f'Vorwegabzug Gewerbe 600,00 € – {REASON}' in answer.json()['lines'][0]['note']
        answer = world.act('GET', url + '/pdf')
        assert answer.status_code == 200, world.describe_response()
        content = subprocess.run(['pdftotext', '-', '-'], input=answer.content, capture_output=True, check=True).stdout.decode()
        compact = ' '.join(content.split())
        assert 'Vorwegabzug Gewerbe 600,00 €' in compact
        assert REASON in compact


@when('the manager opens the commercial position in the draft at desktop and phone widths')
@browser_step
async def commercial_draft_form(browser_world, request):
    page = browser_world.page
    page.on('console', lambda message: browser_world.errors.append(message.text) if message.type == 'error' else None)
    run = browser_world.world.data['commercial']['rows'][1]['run']
    directory = artifact_directory(request.node)
    for width in (1280, 360):
        await page.set_viewport_size({'width': width, 'height': 1000})
        await browser_world.visit(f"/abrechnungen/{run['id']}")
        await expect(page.get_by_role('heading', name='Betriebskostenabrechnung', exact=False)).to_be_visible()
        if directory:
            path = directory / f'draft-{width}.png'
            await page.screenshot(path=str(path), full_page=True)
            attach_artifact(request.node, path)


@when('the manager enters and calculates the reasoned commercial share through the form')
@browser_step
async def commercial_fill_form(browser_world):
    page = browser_world.page
    await page.get_by_label('Vorwegabzug', exact=True).select_option('percent')
    await page.get_by_label('Gewerbeanteil (%)', exact=True).fill('50')
    await page.get_by_label('Begründung', exact=True).fill(' ')
    await page.get_by_role('button', name='Berechnen', exact=True).click()
    await expect(page.get_by_role('alert').filter(has_text='Begründung')).to_be_visible()
    await page.get_by_label('Begründung', exact=True).fill(REASON)
    async with page.expect_response(lambda response: response.url.endswith('/compute')) as pending:
        await page.get_by_role('button', name='Berechnen', exact=True).click()
    answer = await pending.value
    assert answer.status == 200, await answer.text()


@then("the draft shows the commercial deduction and the flat's 321.43 euro share without clipped inputs")
@browser_step
async def commercial_rendered_result(browser_world, request):
    page = browser_world.page
    directory = artifact_directory(request.node)
    for width in (1280, 360):
        await page.set_viewport_size({'width': width, 'height': 1000})
        if width == 360:
            await page.wait_for_function("document.querySelector('aside').getBoundingClientRect().right <= 0")
        await expect(page.get_by_text(f'Vorwegabzug Gewerbe 600,00 € – {REASON}', exact=False)).to_be_visible()
        await expect(page.get_by_role('cell', name='321.43 EUR', exact=True).first).to_be_visible()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if directory:
            await page.get_by_role('region', name='Vorwegabzug Gewerbe', exact=True).evaluate("element => element.scrollIntoView({block: 'start'})")
            path = directory / f'computed-{width}.png'
            await page.screenshot(path=str(path))
            attach_artifact(request.node, path)


@when("Verwaltung Beta tries to open and calculate Alpha's commercial statement")
def foreign_commercial_statement(world):
    import uuid
    data = world.data['commercial']
    other = world.administrator_of('Verwaltung Beta')
    answers = []
    for run_id in (data['rows'][0]['run']['id'], str(uuid.uuid4())):
        url = f'/api/settlements/{run_id}'
        answers.append(world.send(other, 'GET', url))
        for action in ('compute', 'preview', 'finalize'):
            answers.append(world.send(other, 'POST', url + '/' + action,
                json={'commercial_preallocations': [commercial_rule(data)]}))
    world.data['commercial_denied'] = answers


@then("the commercial statement is not found and Alpha's allocation rule is unchanged")
def foreign_commercial_denied(world):
    answers = world.data['commercial_denied']
    assert all(answer.status_code == 404 for answer in answers)
    assert [answer.json() for answer in answers[:4]] == [answer.json() for answer in answers[4:]]
    run = world.data['commercial']['rows'][0]['run']
    answer = world.act('GET', f"/api/settlements/{run['id']}")
    assert answer.status_code == 200, world.describe_response()
    assert answer.json()['extra_inputs']['commercial_preallocations'] == []
