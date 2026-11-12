"""Bind features/dunning_notice_destination.feature to the shared steps."""
import io
import re
from pathlib import Path

import pdfplumber
from pytest_bdd import scenarios, then, when

from tests.bdd.steps.browser_dunning import april_rent, april_reminder  # noqa: F401

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "dunning_notice_destination.feature"))


@when("the manager issues the created reminder")
def issue_created_reminder(world):
    notice = world.response.json()
    answer = world.act("POST", f"/api/dunning/{notice['id']}/issue")
    assert answer.status_code == 200, world.describe_response()
    world.data["issued_notice"] = answer.json()


@then("the issued PDF names the unit, the property and the payment account")
def issued_pdf_names_destination(world):
    answer = world.act("GET", f"/api/dunning/{world.data['issued_notice']['id']}/pdf")
    assert answer.status_code == 200, world.describe_response()
    with pdfplumber.open(io.BytesIO(answer.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert world.data["people"][0].name in text
    assert "Wohnung 1" in text
    assert "Sonnenhof" in text
    assert "DE02120300000000202051" in text
    assert re.search(r"100[.,]00", text), text
