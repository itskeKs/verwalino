"""Journey regression for genuinely missing financial detail records."""
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import scenarios, when, then

scenarios(str(Path(__file__).resolve().parents[3] / "features/journeys/missing_weg_objects.feature"))


@when("I open a missing payment, economic plan and WEG settlement on desktop and phone", target_fixture="missing_views")
def missing_views(browser_world):
    b = browser_world

    async def visit_missing():
        views = []
        for width in (1280, 360):
            await b.page.set_viewport_size({"width": width, "height": 900})
            for route, message in (
                ("zahlungen", "Zahlung nicht gefunden."),
                ("wirtschaftsplaene", "Wirtschaftsplan nicht gefunden."),
                ("weg-abrechnungen", "WEG-Abrechnung nicht gefunden."),
            ):
                await b.visit(f"/{route}/00000000-0000-0000-0000-000000000000")
                alert = b.page.locator("p.error-text")
                await expect(alert).to_have_text(message)
                assert await b.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                views.append(await alert.inner_text())
        return views

    return b.run(visit_missing())


@then("each page explains the missing record in German without a technical error prefix")
def german_missing_views(missing_views):
    assert missing_views == ["Zahlung nicht gefunden.", "Wirtschaftsplan nicht gefunden.",
                             "WEG-Abrechnung nicht gefunden."] * 2
