"""Exercise explicit bank successor selection on the real intake screen."""
import os
from pathlib import Path

import pytest_asyncio
from playwright.async_api import expect
from pytest_bdd import given, scenario, then, when

from app.core.config import settings
from app.core.storage import LocalFsDocumentStore, set_store
from tests.bdd.steps.browser_common import browser_step
from tests.conftest import _make_session_cookie
from tests.test_allocation_adoptions import confirm_grant
from tests.test_allocation_correction_preview import prepared
from tests.test_allocation_corrections import split_debit
from tests.test_financial_scope_boundary import scope_seed as scope_seed, worker_tenant as worker_tenant
from tests.test_invoice_source_custody import fixture
from tests.test_scoped_account_statements import client


@scenario('../../../features/bank_cost_correction.feature',
          'A manager carries one covered position to its chosen replacement')
def test_reviewed_cost_correction():
    pass


@pytest_asyncio.fixture
async def covered_allocation(migrator_session, scope_seed, browser_storage):
    ids = await fixture(migrator_session, scope_seed)
    await confirm_grant(ids)
    set_store(LocalFsDocumentStore(browser_storage))
    try:
        async with client(ids) as api:
            old, proposal, _, _ = await prepared(api, ids)
            bank, debit = await split_debit(api, ids)
            original = old['costs'][1]['cost_entry_id']
            result = await api.post(f'/api/bank-transactions/{debit}/assign', json={'cost_entry_id': original})
            assert result.status_code == 200, result.text
        return ids, proposal, bank, debit, original
    finally:
        set_store(None)


@given('a reviewed 119 euro allocation has a 60 euro debit on its second part')
@browser_step
async def open_review(browser_world, covered_allocation):
    ids, proposal, _, _, _ = covered_allocation
    context = browser_world.context
    await context.clear_cookies()
    await context.add_cookies([{
        'name': settings.session_cookie_name,
        'value': _make_session_cookie(ids['clerk'], ids['org']),
        'url': browser_world.session.base_url,
    }])
    await browser_world.visit(f"/posteingang/belege/{proposal['document_ref_id']}")
    await expect(browser_world.page.get_by_role('heading', name='Belege und Vorschläge')).to_be_visible()
    await browser_world.page.get_by_role('button', name='Korrekturvorschau laden', exact=True).click()


@when('the manager assigns that coverage to the third proposed part at desktop and mobile widths')
@browser_step
async def confirm_successor(browser_world):
    page = browser_world.page
    section = page.get_by_role('region', name='Korrekturfolgen')
    reason = section.get_by_label('Begründung der Korrektur')
    await expect(reason).to_be_visible()
    selector = section.get_by_role('combobox', name='Nachfolger für Teil 2 (60,00 € zugeordnet)')
    submit = section.get_by_role('button', name='Gesamte Aufteilung verbindlich korrigieren')
    await reason.fill('Synthetic corrected allocation')
    for checkbox in await section.get_by_role('checkbox').all():
        await checkbox.check()
    for width in (1280, 360):
        await page.set_viewport_size({'width': width, 'height': 1000})
        present = await selector.count()
        if present:
            await selector.select_option(index=0)
            await expect(submit).to_be_disabled()
            await selector.select_option(label='Teil 3 · 60,00 €')
            assert await selector.evaluate('element => element.getBoundingClientRect().right <= innerWidth')
            await expect(submit).to_be_enabled()
        if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1':
            directory = Path(__file__).resolve().parents[3] / 'docs/assets/design/bank-cost-correction'
            directory.mkdir(parents=True, exist_ok=True)
            await (selector if present else reason).scroll_into_view_if_needed()
            await page.screenshot(path=directory / f"{'after' if present else 'before'}-{width}.png")
    await expect(selector).to_be_visible()
    await submit.click()
    await expect(page.get_by_role('heading', name='Aufteilung korrigiert')).to_be_visible()


@then('the correction leaves the first two parts open and the third part fully covered')
@browser_step
async def exact_coverage(browser_world, covered_allocation):
    _, proposal, bank, debit, original = covered_allocation
    request = browser_world.page.request
    result = await request.get(f"/api/logical-documents/{proposal['logical_document_id']}/allocation-adoptions")
    assert result.ok
    costs = (await result.json())['item']['costs']
    for cost, expected in zip(costs, ('19.00', '40.00', '0.00'), strict=True):
        response = await request.get(f"/api/cost-entries/{cost['cost_entry_id']}")
        assert response.ok and (await response.json())['bank_open_amount'] == expected
    response = await request.get(f'/api/bank-transactions?bank_account_id={bank}&status=ZUGEORDNET')
    assert response.ok
    assert [(row['id'], row['cost_entry_id']) for row in (await response.json())['items']] == [(debit, original)]
