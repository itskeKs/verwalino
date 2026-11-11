"""Property list paging and bounded filters through the real UI."""
from playwright.async_api import expect
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.browser_common import assert_fits_mobile, button, field
from tests.bdd.steps.browser_setup import create

SHARED_NAME = "Paging-selector-acceptance"


@given(parsers.parse('55 properties named "{name}" exist'))
def same_name_properties(world, name, request):
    ids = [create(world, "/api/properties", {"name": name, "management_type": "MIET"})["id"] for _ in range(55)]

    def remove_them():
        # The organization is shared by every browser feature: a long property list would
        # slow the property pickers and dashboards of the scenarios that run after this one.
        for property_id in ids:
            answer = world.act("DELETE", f"/api/properties/{property_id}")
            assert answer.status_code == 204, world.describe_response()

    request.addfinalizer(remove_them)


@when(parsers.parse('the manager searches properties for "{text}"'))
def search_properties(browser_world, text):
    async def run():
        page = browser_world.page
        await browser_world.visit("/objekte")
        await field(page, "Objekte suchen").fill(text)
        await button(page.get_by_role("search"), "Suchen").click()
    browser_world.run(run())


@then(parsers.parse("the property list shows {count:d} rows"))
def property_rows(browser_world, count):
    async def run():
        await browser_world.page.wait_for_function(
            f"() => document.querySelectorAll('tbody tr').length === {count}")
    browser_world.run(run())


@when("the manager loads the next properties")
def load_more(browser_world):
    async def run():
        await button(browser_world.page, "Weitere laden").click()
    browser_world.run(run())


@then(parsers.parse("the property list shows {count:d} unique property links"))
def unique_property_links(browser_world, count):
    async def run():
        page = browser_world.page
        await page.wait_for_function(f"() => document.querySelectorAll('tbody tr').length === {count}")
        links = await page.locator("tbody a").evaluate_all("nodes => nodes.map(node => node.getAttribute('href'))")
        assert len(set(links)) == count, f"{len(links)} links, {len(set(links))} unique"
    browser_world.run(run())


@then("the page fits a 390 pixel wide viewport")
def fits_mobile(browser_world):
    browser_world.run(assert_fits_mobile(browser_world.page))


@when("the manager opens the bank accounts")
def open_bank_accounts(browser_world):
    async def run():
        await browser_world.visit("/bankkonten")
        await browser_world.page.get_by_label("Nach Objekt filtern", exact=True).locator("option").nth(1).wait_for(state="attached")
    browser_world.run(run())


@then("the property filter offers at most 51 entries")
def bounded_filter(browser_world):
    async def run():
        options = await browser_world.page.get_by_label("Nach Objekt filtern", exact=True).locator("option").count()
        assert options <= 51, f"unbounded filter with {options} entries"
    browser_world.run(run())


@when("the manager selects the first property in the filter")
def select_first_property(browser_world):
    async def run():
        page = browser_world.page
        selector = page.get_by_label("Nach Objekt filtern", exact=True)
        chosen = await selector.locator("option").nth(1).get_attribute("value")
        assert chosen
        browser_world.world.data["filter_property"] = chosen
        await selector.select_option(chosen)
    browser_world.run(run())


@when(parsers.parse('the manager searches the property filter for "{text}"'))
def search_property_filter(browser_world, text):
    async def run():
        await field(browser_world.page, "Objekte durchsuchen").fill(text)
    browser_world.run(run())


@then("the property filter still holds the selected property")
def filter_retains_selection(browser_world):
    async def run():
        page = browser_world.page
        selector = page.get_by_label("Nach Objekt filtern", exact=True)
        await page.wait_for_function(
            "() => document.querySelectorAll('select[aria-label=\"Nach Objekt filtern\"] option').length <= 2")
        await expect(selector).to_have_value(browser_world.world.data["filter_property"])
    browser_world.run(run())


@when(parsers.parse('the manager searches persons for "{text}"'))
def search_persons(browser_world, text):
    async def run():
        page = browser_world.page
        await browser_world.visit("/personen")
        await field(page, "Personen suchen").fill(text)
        await button(page.get_by_role("search"), "Suchen").click()
    browser_world.run(run())


@then("the page reports that no persons exist")
def no_persons(browser_world):
    async def run():
        await expect(browser_world.page.get_by_text("Keine Personen vorhanden.", exact=True)).to_be_visible()
    browser_world.run(run())
